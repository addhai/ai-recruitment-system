"""跨副本 / 跨重启的共享「冷却期」记录。

登录失败限流（`login_guard`）与 LLM 供应商熔断（`circuit_breaker`）原本只把
状态放在**进程内存**里，于是：多副本部署时各副本独立计数（实际阈值 ≈ 副本数 ×
配置值）、进程重启即清零。这里用项目本来就有的数据库存一份共享记录来消除它。

## 为什么是数据库而不是 Redis

限流/熔断是**加固手段**，为它新引入一个必须高可用的中间件（Redis）不划算：
运维多一个组件、失效场景多一类。数据库已经是部署的硬依赖，而这两个机制
**写入极少**（只在"跨过阈值/熔断"那一刻写一条）、读取也轻（登录路径本就要
查 `users` 表），因此数据库完全够用，且不新增依赖。

## 有意做出的取舍

1. **只写"已经触发"的事实，不写每一次失败**。进程内的失败计数保持原样
   （它快、无 IO、单进程下判定的就是它）；只有某个 key 跨过阈值/熔断时才写
   一条共享记录。`/auth/login` 是**唯一无鉴权的写路径**，若每次失败都写库，
   攻击者就把限流本身变成了数据库写放大器。
2. **只有 `until`（冷却截止时刻），没有计数器**。共享记录只表达"这个 key 在
   什么时刻之前应被拒绝"，不参与计数——因此不需要跨进程的原子递增，也就没有
   竞态。
3. **失败开放（best-effort）**：数据库不可用时读/写都只记 warning、绝不外抛。
   此时退化成改动前的单进程行为（而不是变成完全没限流），因为限流是加固手段，
   不该因其存储故障把登录整体打挂。
4. **熔断的共享记录只写不清**：本地状态机负责恢复，共享记录到期自然失效；
   不因为一次成功就去删别的副本写下的记录。
5. **本地阈值配置为 0（关闭）时不因共享记录而拦截**：该判断在调用方
   （`login_guard` / `circuit_breaker`）做，见那边的 `limit > 0` / `ENABLED` 前置判断。

## 时间

`until` 用 **UTC naive 墙钟**（`datetime.utcnow()`，与项目其它时间列一致）：
共享记录要跨进程可比，不能用单调钟。进程内计数仍用各自的 `time.monotonic`。
`_now_utc()` 是可注入钩子，测试里与假时钟联动手动推进。
"""
from datetime import datetime, timedelta
from typing import Optional

from src.logging_setup import get_logger
from src.models.database import Cooldown, SessionLocal

logger = get_logger(__name__)

# 每个 kind 最多保留的行数：防止攻击者用海量随机用户名把表撑大
# （对应 login_guard._MAX_KEYS 的思路）。超出时丢弃 until 最早的若干条，
# 因为它们最先失效、对"当前是否该拦"贡献最小。
_MAX_ROWS_PER_KIND = 5000

# 时间源做成模块级间接引用：测试可只替换它，把假时钟同时推进两边
_now_utc = datetime.utcnow


def blocked_until(kind: str, key: str) -> Optional[datetime]:
    """该 key 的冷却截止时刻；未冷却（无记录或已过期）返回 None。

    best-effort：数据库不可用时记 warning 并返回 None（退化为单进程行为）。
    """
    try:
        now = _now_utc()
        db = SessionLocal()
        try:
            row = (db.query(Cooldown)
                   .filter(Cooldown.kind == kind, Cooldown.key == key)
                   .first())
            if row is None or row.until is None:
                return None
            return row.until if row.until > now else None
        finally:
            db.close()
    except Exception as e:
        logger.warning("读共享冷却记录失败",
                       extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})
        return None


def mark(kind: str, key: str, seconds: float) -> None:
    """把该 key 标记为 `utcnow + seconds` 之前应被拒绝（写入或刷新）。

    重复 mark 取**更晚**的 `until`（不因为一次较短的 mark 而提前解除已有的
    冷却）。顺手清理已过期行——写入很少，这样足够，不必另设清理任务。
    best-effort：任何异常只记 warning，绝不外抛。
    """
    if seconds is None or seconds <= 0:
        return
    try:
        now = _now_utc()
        until = now + timedelta(seconds=seconds)
        db = SessionLocal()
        try:
            row = (db.query(Cooldown)
                   .filter(Cooldown.kind == kind, Cooldown.key == key)
                   .first())
            if row is None:
                db.add(Cooldown(kind=kind, key=key, until=until, created_at=now))
            elif row.until is None or until > row.until:
                row.until = until
            # 顺手清理已过期的记录（本次写入的 until 一定 > now，不会被误删）
            db.query(Cooldown).filter(Cooldown.until <= now).delete(
                synchronize_session=False)
            db.flush()
            _trim_kind(db, kind)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("写共享冷却记录失败",
                       extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})


def clear(kind: str, key: str) -> None:
    """删除该 key 的共享记录。best-effort，异常只记 warning。"""
    try:
        db = SessionLocal()
        try:
            db.query(Cooldown).filter(
                Cooldown.kind == kind, Cooldown.key == key).delete(
                synchronize_session=False)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("清共享冷却记录失败",
                       extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})


def _trim_kind(db, kind: str) -> None:
    """单 kind 行数超上限时，删除 `until` 最早的若干条（调用方已持 session）。"""
    total = db.query(Cooldown).filter(Cooldown.kind == kind).count()
    excess = total - _MAX_ROWS_PER_KIND
    if excess <= 0:
        return
    rows = (db.query(Cooldown)
            .filter(Cooldown.kind == kind)
            .order_by(Cooldown.until.asc())
            .limit(excess)
            .all())
    for row in rows:
        db.delete(row)


def reset_for_test() -> None:
    """测试用：清空整张表。best-effort，异常只记 warning。"""
    try:
        db = SessionLocal()
        try:
            db.query(Cooldown).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("清理共享冷却表失败",
                       extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})
