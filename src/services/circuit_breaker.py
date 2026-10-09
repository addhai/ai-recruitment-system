"""LLM 供应商熔断器。

## 解决什么

供应商整体故障（宕机、网络不通、持续 5xx）时，每个调用仍会耗完自己的重试预算
（`max_retries=2` + 超时）才降级。一次简历评估有 4~8 次调用，串起来就是几分钟的
纯等待，而且期间候选人的流程全部卡在那里。

熔断的作用是：连续失败到阈值后**直接快速失败**，等冷却期过了再放一个探针请求
试探；探针成功就恢复，失败就继续熔断。

## 什么才算"失败"（这个划分比熔断本身更重要）

只有**请求层面的失败**才计入：连不上、超时、HTTP 5xx/429。
以下都**不能**计入，否则会把熔断对业务问题误触发：

- **JSON 解析失败**：模型答了，只是答得不是合法 JSON。这是输出质量问题，
  调温度/改 prompt 才是解法；把它算作供应商故障会导致"模型偶尔不听话"
  直接熔断，反而把可用性做低了。
- **预算耗尽**：根本没发请求。
- **输入被护栏拦截**：同上。

## 状态机

    closed --连续失败达阈值--> open
    open --冷却到期--> half_open（只放一个探针）
    half_open --探针成功--> closed
    half_open --探针失败--> open（重新计时）

half_open 只放一个探针，是为了避免冷却刚到期就把积压的请求一起打过去——
那正是刚恢复的供应商最扛不住的时刻。

## 多副本 / 重启（共享冷却记录）

本地状态机仍是判定的主体；但每次**真正触发熔断**（连续失败达阈值进入 open、
或 half_open 探针失败重新 open）时，会向数据库写一条共享冷却记录，
`allow()` 在本地逻辑之前先查它。于是另一个副本触发的熔断对本副本同样生效，
本进程重启后冷却期内也仍会被拦。

共享记录**只写不清**：恢复由本地状态机负责，共享记录到期自然失效——不因为
一次成功就去删别的副本写下的记录（那会让它们误以为供应商已恢复）。
`LLM_CIRCUIT_BREAKER_ENABLED=False` 时完全不读不写共享记录，保证"关掉熔断"
这个开关有效。共享存储不可用时只记 warning，`allow()` 退化为本地判定。
"""
import threading
import time
from typing import Optional

from src.config import settings
from src.logging_setup import get_logger
from src.services import shared_cooldown

logger = get_logger(__name__)

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"

# 共享冷却记录只服务于"默认供应商"这一个熔断对象，key 固定为 default
_SHARED_KIND = "llm_circuit"
_SHARED_KEY = "default"

# 时间源做成模块级间接引用：测试可只替换它，不必 patch 全局 time.monotonic
_now = time.monotonic


class CircuitOpen(RuntimeError):
    """熔断打开，本次调用被直接拒绝（未发出请求）。"""


class _Breaker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self._probe_taken = False   # half_open 下是否已有探针在飞

    def reset(self) -> None:
        with self._lock:
            self.state = CLOSED
            self.failures = 0
            self.opened_at = 0.0
            self._probe_taken = False

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self.state, "failures": self.failures}

    def allow(self) -> bool:
        """是否可以发起本次调用。返回 False 表示应快速失败。"""
        if not settings.LLM_CIRCUIT_BREAKER_ENABLED:
            return True
        # 共享冷却（另一个副本已熔断 / 本进程刚重启）：冷却中直接拒绝，
        # 且**不改动本地状态机**——否则会白白消耗本地的 half_open 探针名额。
        if shared_cooldown.blocked_until(_SHARED_KIND, _SHARED_KEY) is not None:
            return False
        with self._lock:
            if self.state == CLOSED:
                return True
            if self.state == OPEN:
                if _now() - self.opened_at < settings.LLM_CIRCUIT_OPEN_SECONDS:
                    return False
                # 冷却到期 -> half_open，放一个探针
                self.state = HALF_OPEN
                self._probe_taken = True
                return True
            # half_open：只放一个探针，其余快速失败
            if self._probe_taken:
                return False
            self._probe_taken = True
            return True

    def record_success(self) -> None:
        with self._lock:
            if self.state != CLOSED or self.failures:
                logger.info("LLM 熔断恢复", extra={"status": "circuit_closed",
                                                   "prev_state": self.state})
            self.state = CLOSED
            self.failures = 0
            self._probe_taken = False

    def record_failure(self) -> None:
        if not settings.LLM_CIRCUIT_BREAKER_ENABLED:
            return          # 关闭时完全不参与，状态也不会莫名变成 open
        opened = False
        with self._lock:
            threshold = settings.LLM_CIRCUIT_FAILURE_THRESHOLD
            if self.state == HALF_OPEN:
                # 探针失败：重新进入 open，重新计时
                self.state = OPEN
                self.opened_at = _now()
                self._probe_taken = False
                opened = True
                logger.warning("LLM 熔断探针失败，继续熔断",
                               extra={"status": "circuit_open",
                                      "threshold": threshold})
            else:
                self.failures += 1
                if threshold > 0 and self.failures >= threshold:
                    self.state = OPEN
                    self.opened_at = _now()
                    self._probe_taken = False
                    opened = True
                    logger.warning(
                        f"LLM 连续失败 {self.failures} 次，熔断 "
                        f"{settings.LLM_CIRCUIT_OPEN_SECONDS}s",
                        extra={"status": "circuit_open", "failures": self.failures})
        # 真正触发熔断才写共享记录；放锁外，避免持锁做 IO
        if opened:
            shared_cooldown.mark(_SHARED_KIND, _SHARED_KEY,
                                 settings.LLM_CIRCUIT_OPEN_SECONDS)


_breaker = _Breaker()


def allow() -> bool:
    return _breaker.allow()


def record_success() -> None:
    _breaker.record_success()


def record_failure() -> None:
    _breaker.record_failure()


def snapshot() -> dict:
    """当前状态，供接口/日志查看。"""
    return _breaker.snapshot()


def reset_for_test() -> None:
    _breaker.reset()
