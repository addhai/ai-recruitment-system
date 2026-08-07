"""SSE 通知工具测试：确认广播函数不抛异常（不依赖真实 WebSocket 连接）。"""
import asyncio

from src.sse.notification import (
    notify_workflow_progress,
    notify_interview_scheduled,
    notify_evaluation_added,
)


def test_notify_workflow_progress_no_error():
    asyncio.run(
        notify_workflow_progress(
            candidate_id=1, progress=50, step="测评", details={"msg": "进行中"}
        )
    )


def test_notify_interview_scheduled_no_error():
    asyncio.run(
        notify_interview_scheduled(
            candidate_id=1, interview_id=1, round=1, scheduled_at="2026-08-07"
        )
    )


def test_notify_evaluation_added_no_error():
    asyncio.run(
        notify_evaluation_added(candidate_id=1, dimension="技术", score=80)
    )
