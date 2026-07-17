import asyncio
from typing import Dict, List, Any, Callable
from datetime import datetime
import json

subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
lock = asyncio.Lock()


async def subscribe(user_id: str, callback: Callable[[Dict[str, Any]], None]):
    async with lock:
        if user_id not in subscribers:
            subscribers[user_id] = []
        subscribers[user_id].append(callback)


async def unsubscribe(user_id: str, callback: Callable[[Dict[str, Any]], None]):
    async with lock:
        if user_id in subscribers:
            if callback in subscribers[user_id]:
                subscribers[user_id].remove(callback)
            if not subscribers[user_id]:
                del subscribers[user_id]


async def broadcast(notification: Dict[str, Any]):
    async with lock:
        notification["timestamp"] = datetime.utcnow().isoformat()
        message = json.dumps(notification)
        
        for user_id, callbacks in list(subscribers.items()):
            if notification.get("target_users") is None or user_id in notification.get("target_users", []):
                for callback in callbacks:
                    try:
                        callback(message)
                    except Exception:
                        pass


async def notify_workflow_progress(candidate_id: int, progress: int, step: str, details: Dict[str, Any] = None):
    await broadcast({
        "type": "workflow_progress",
        "candidate_id": candidate_id,
        "progress": progress,
        "step": step,
        "details": details or {}
    })


async def notify_interview_scheduled(candidate_id: int, interview_id: int, round: int, scheduled_at: str):
    await broadcast({
        "type": "interview_scheduled",
        "candidate_id": candidate_id,
        "interview_id": interview_id,
        "round": round,
        "scheduled_at": scheduled_at
    })


async def notify_interview_completed(candidate_id: int, interview_id: int, score: int, feedback: str):
    await broadcast({
        "type": "interview_completed",
        "candidate_id": candidate_id,
        "interview_id": interview_id,
        "score": score,
        "feedback": feedback
    })


async def notify_questionnaire_generated(candidate_id: int, questionnaire_id: int, type: str):
    await broadcast({
        "type": "questionnaire_generated",
        "candidate_id": candidate_id,
        "questionnaire_id": questionnaire_id,
        "type": type
    })


async def notify_questionnaire_submitted(candidate_id: int, questionnaire_id: int, score: int):
    await broadcast({
        "type": "questionnaire_submitted",
        "candidate_id": candidate_id,
        "questionnaire_id": questionnaire_id,
        "score": score
    })


async def notify_hiring_decision(candidate_id: int, decision: str, overall_score: int):
    await broadcast({
        "type": "hiring_decision",
        "candidate_id": candidate_id,
        "decision": decision,
        "overall_score": overall_score
    })


async def notify_candidate_added(candidate_id: int, candidate_name: str):
    await broadcast({
        "type": "candidate_added",
        "candidate_id": candidate_id,
        "candidate_name": candidate_name
    })


async def notify_candidate_status_changed(candidate_id: int, old_status: str, new_status: str):
    await broadcast({
        "type": "candidate_status_changed",
        "candidate_id": candidate_id,
        "old_status": old_status,
        "new_status": new_status
    })


async def notify_evaluation_added(candidate_id: int, dimension: str, score: int):
    await broadcast({
        "type": "evaluation_added",
        "candidate_id": candidate_id,
        "dimension": dimension,
        "score": score
    })


async def notify_system_message(message: str, level: str = "info"):
    await broadcast({
        "type": "system_message",
        "message": message,
        "level": level
    })


def get_active_subscribers() -> int:
    return sum(len(callbacks) for callbacks in subscribers.values())
