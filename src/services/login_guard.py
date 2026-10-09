"""登录失败限流（防爆破 / 防撞库）。

## 为什么需要

`/auth/login` 是**唯一无需凭据即可调用的写路径**，而演示/种子账号是
`admin123` 这类弱口令。没有失败计数时，只要端口可达就等于可被持续爆破。

## 为什么要记两个维度

只按用户名记的话，"一个口令试遍所有用户名"的**撞库**攻击可以完全绕过它——
攻击者对每个账号只试 4 次，永远碰不到阈值。所以同时按来源 IP 记一份，
阈值放宽（见 settings.LOGIN_MAX_FAILED_PER_IP），避免办公室共用出口 IP 被误伤。

## 有意不做的两件事

1. **失败一律计数，不管用户名是否存在**。否则"存在才计数"会变成用户枚举探针：
   攻击者先撞 5 次，若被锁定就说明该账号存在。
2. **不在响应里区分"用户不存在"和"密码错误"**——保持现有的统一 401 文案。

## 多副本 / 重启（当前实现）

失败计数仍在**进程内存**里（快、无 IO，单进程下判定的就是它）；但一旦某个维度
跨过阈值，就会向数据库写一条**共享冷却记录**（见 `shared_cooldown`），于是：
- 多副本部署时，任一副本触发的锁定对其它副本也生效；
- 进程重启后，内存计数清零，但冷却期内的共享记录仍在，仍会被拦。

## 仍然存在的局限（不假装它严密）

- 共享记录只表达"冷却到某时刻"，**不参与计数**：它不能让多副本的失败次数合并
  计数，只是把"已触发"的结论广播出去。因此各副本仍需各自累积到各自阈值才会
  触发（阈值本身不再被放大成 副本数 × 阈值 的效果，因为触发的锁定是共享的）。
- 数据库不可用时，共享读/写都只记 warning 并退化为**单进程行为**（限流是加固
  手段，不该因存储故障把登录整体打挂）；此时该进程内的限流照常生效。
"""
import math
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from src.config import settings
from src.services import shared_cooldown

# 共享记录的维度名：内存 key 形如 u:alice / ip:1.2.3.4，前缀在共享层是冗余的，
# 由 kind 区分维度，key 存去前缀后的原值。
_KIND_USER = "login_user"
_KIND_IP = "login_ip"

# key -> 失败时间戳（单调钟，不受系统时间调整影响）
_failures: Dict[str, Deque[float]] = {}
_lock = threading.Lock()

# key 数量上限：防止攻击者用海量随机用户名把内存撑爆
_MAX_KEYS = 5000

# 时间源做成模块级间接引用：测试可以只替换它，
# 而不必去 patch 全局的 time.monotonic（那会波及 asyncio、httpx 等所有使用者）
_now = time.monotonic


class LoginThrottled(Exception):
    """该用户名或来源 IP 处于限流窗口内。"""

    def __init__(self, retry_after: int):
        self.retry_after = max(1, int(retry_after))
        super().__init__(f"too many failed attempts, retry after {self.retry_after}s")


def _user_key(username: str) -> str:
    return f"u:{(username or '').strip().lower()}"


def _ip_key(ip: Optional[str]) -> Optional[str]:
    return f"ip:{ip}" if ip else None


def _shared_key(mem_key: str) -> str:
    """内存 key（u:alice / ip:1.2.3.4）-> 共享 key（alice / 1.2.3.4）。

    共享记录按 kind 区分维度，前缀是冗余的；去掉它让表里存的是可直接解读的原值。
    """
    return mem_key.split(":", 1)[1]


def _prune(dq: Deque[float], now: float, window: float) -> None:
    while dq and now - dq[0] > window:
        dq.popleft()


def _state(key: Optional[str], limit: int, window: float,
           lockout: float) -> Tuple[bool, int]:
    if not key or limit <= 0:
        return False, 0
    now = _now()
    dq = _failures.get(key)
    if not dq:
        return False, 0
    _prune(dq, now, window)
    if len(dq) < limit:
        return False, 0
    until = dq[-1] + lockout
    if now < until:
        return True, int(until - now) + 1
    # 锁定已过期：清空窗口，否则残留的失败记录会让下一次登录立刻又被锁
    dq.clear()
    return False, 0


def _shared_retry(kind: str, mem_key: Optional[str], limit: int) -> Optional[int]:
    """查共享冷却；命中返回剩余秒数（>=1），未命中或该维度已关闭返回 None。

    先判 `limit > 0`：否则"把阈值配成 0 关掉限流"这个开关会因为别的副本残留的
    共享记录而失效。查询放锁外，避免持锁做 IO。
    """
    if not mem_key or limit <= 0:
        return None
    until = shared_cooldown.blocked_until(kind, _shared_key(mem_key))
    if until is None:
        return None
    remaining = (until - shared_cooldown._now_utc()).total_seconds()
    return max(1, int(math.ceil(remaining)))


def check_allowed(username: str, ip: Optional[str] = None) -> None:
    """登录前调用；处于限流窗口内则抛 LoginThrottled。"""
    with _lock:
        blocked, retry = _state(_user_key(username),
                                settings.LOGIN_MAX_FAILED_PER_USER,
                                settings.LOGIN_FAILURE_WINDOW_SECONDS,
                                settings.LOGIN_LOCKOUT_SECONDS)
        if not blocked:
            blocked, retry = _state(_ip_key(ip),
                                    settings.LOGIN_MAX_FAILED_PER_IP,
                                    settings.LOGIN_FAILURE_WINDOW_SECONDS,
                                    settings.LOGIN_LOCKOUT_SECONDS)
    if blocked:
        raise LoginThrottled(retry)
    # 本地没拦：再查共享冷却（覆盖"另一个副本已锁定"与"本进程刚重启"两种情况）
    retry = _shared_retry(_KIND_USER, _user_key(username),
                          settings.LOGIN_MAX_FAILED_PER_USER)
    if retry is None:
        retry = _shared_retry(_KIND_IP, _ip_key(ip),
                              settings.LOGIN_MAX_FAILED_PER_IP)
    if retry is not None:
        raise LoginThrottled(retry)


def record_failure(username: str, ip: Optional[str] = None) -> None:
    """一次失败登录。**无论用户名是否存在都要调用**，否则会变成用户枚举探针。"""
    now = _now()
    window = settings.LOGIN_FAILURE_WINDOW_SECONDS
    dimensions = (
        (_user_key(username), _KIND_USER, settings.LOGIN_MAX_FAILED_PER_USER),
        (_ip_key(ip), _KIND_IP, settings.LOGIN_MAX_FAILED_PER_IP),
    )
    triggered: List[Tuple[str, str]] = []
    with _lock:
        for mem_key, _, _ in dimensions:
            if not mem_key:
                continue
            dq = _failures.setdefault(mem_key, deque())
            dq.append(now)
            _prune(dq, now, window)
        _evict_if_needed()
        # 判定哪些维度已跨过阈值；IO 放到锁外，避免持锁写库
        for mem_key, kind, limit in dimensions:
            if not mem_key or limit <= 0:
                continue
            blocked, _retry = _state(mem_key, limit, window,
                                     settings.LOGIN_LOCKOUT_SECONDS)
            if blocked:
                triggered.append((kind, _shared_key(mem_key)))
    # 只把"已经触发"这一事实写进共享存储（不写每一次失败）：
    # /auth/login 是唯一无鉴权的写路径，逐次写库会把限流变成数据库写放大器。
    for kind, skey in triggered:
        shared_cooldown.mark(kind, skey, settings.LOGIN_LOCKOUT_SECONDS)


def record_success(username: str, ip: Optional[str] = None) -> None:
    """登录成功：清掉该用户名的失败记录（不清 IP，避免一处成功就洗白整段攻击）"""
    with _lock:
        _failures.pop(_user_key(username), None)
    # 共享层同样只清用户名维度：IP 维度保持"一次成功不能洗白整段攻击"的语义
    if settings.LOGIN_MAX_FAILED_PER_USER > 0:
        shared_cooldown.clear(_KIND_USER, _shared_key(_user_key(username)))


def _evict_if_needed() -> None:
    """key 数超上限时，丢弃最久没有失败记录的那些（调用方需已持锁）。"""
    if len(_failures) <= _MAX_KEYS:
        return
    # 按最近一次失败时间排序，先丢最旧的
    ordered = sorted(_failures.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0)
    for key, _ in ordered[: len(_failures) - _MAX_KEYS]:
        _failures.pop(key, None)


def reset_for_test() -> None:
    """测试用：清空全部计数"""
    with _lock:
        _failures.clear()
