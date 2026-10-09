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

## 已知局限（不假装它严密）

状态在**进程内存**里：
- 多副本部署时各副本独立计数，实际阈值 ≈ 副本数 × 阈值；
- 进程重启即清零。

要严格生效需要共享存储（Redis/队列）。本项目当前没有引入那类组件，
且单副本是默认部署形态，所以这里选择"明显提高爆破成本、且不给运维加依赖"的折中。
真要上多副本时，把 `_failures` 换成 Redis 计数器即可，调用方无需改动。
"""
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from src.config import settings

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


def record_failure(username: str, ip: Optional[str] = None) -> None:
    """一次失败登录。**无论用户名是否存在都要调用**，否则会变成用户枚举探针。"""
    now = _now()
    window = settings.LOGIN_FAILURE_WINDOW_SECONDS
    with _lock:
        for key in (_user_key(username), _ip_key(ip)):
            if not key:
                continue
            dq = _failures.setdefault(key, deque())
            dq.append(now)
            _prune(dq, now, window)
        _evict_if_needed()


def record_success(username: str, ip: Optional[str] = None) -> None:
    """登录成功：清掉该用户名的失败记录（不清 IP，避免一处成功就洗白整段攻击）"""
    with _lock:
        _failures.pop(_user_key(username), None)


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
