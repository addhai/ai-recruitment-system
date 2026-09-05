# -*- coding: utf-8 -*-
"""SSE 通知中枢与评估追踪器单测：订阅/广播/派生通知函数/统计。"""
import asyncio

from src.sse import notification as sse
from src.evaluation import tracker


# ---------------------------------------------------------------- 订阅与广播
def test_subscribe_then_broadcast_receives():
    received = []

    async def _main():
        # callback 必须是普通函数：broadcast 内部同步调用，
        # 且收到的是 JSON 字符串（broadcast 序列化后派发）
        def cb(msg):
            received.append(msg)

        await sse.subscribe("user-A", cb)
        await sse.broadcast({"type": "ping", "data": 1})
        await sse.unsubscribe("user-A", cb)
        await sse.broadcast({"type": "ping", "data": 2})  # 已退订，不再收到

    asyncio.run(_main())
    assert len(received) == 1
    assert '"data": 1' in received[0] and '"type": "ping"' in received[0]


def test_broadcast_to_multiple_subscribers():
    got_a, got_b = [], []

    async def _main():
        await sse.subscribe("A", lambda m: got_a.append(m))
        await sse.subscribe("B", lambda m: got_b.append(m))
        await sse.broadcast({"type": "x"})
        assert len(got_a) == 1 and len(got_b) == 1

    asyncio.run(_main())


# ---------------------------------------------------------------- 派生通知函数（无订阅者时广播也不抛错）
def test_all_notify_helpers_safe_without_subscribers():
    async def _main():
        await sse.notify_interview_completed(1, 2, 85, "不错")
        await sse.notify_questionnaire_generated(1, 3, "ai_auto")
        await sse.notify_questionnaire_submitted(1, 3, 77)
        await sse.notify_hiring_decision(1, "推荐录用", 88)
        await sse.notify_candidate_added(1, "新候选人")
        await sse.notify_candidate_status_changed(1, "pending", "screening")
        await sse.notify_evaluation_added(1, "技术", 90)
        await sse.notify_system_message("系统维护通知", "warn")

    asyncio.run(_main())  # 全部不抛错即通过


def test_get_active_subscribers_count():
    async def _main():
        await sse.subscribe("u1", lambda m: None)
        await sse.subscribe("u2", lambda m: None)
        return sse.get_active_subscribers()

    count = asyncio.run(_main())
    assert count >= 2


# ---------------------------------------------------------------- 评估追踪器
def test_tracker_record_and_stats():
    tr = tracker.evaluation_tracker  # 全局单例
    tr.record(
        candidate_id=101, scores={"overall_score": 85, "skill_match_score": 90},
        decision="推荐录用", duration_ms=1234,
    )
    stats = tr.stats()
    assert stats["total_evaluations"] >= 1
    assert stats["avg_overall_score"] > 0
    assert stats["decision_distribution"].get("推荐录用", 0) >= 1


def test_tracker_get_records_limit():
    tr = tracker.evaluation_tracker
    for i in range(5):
        tr.record(candidate_id=200 + i, scores={"overall_score": 80}, decision="待定", duration_ms=100)
    records = tr.get_records(limit=3)
    assert len(records) <= 3
    assert all("candidate_id" in r for r in records)
