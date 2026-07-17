import asyncio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from src.api.auth import get_current_user
from src.sse.notification import subscribe, unsubscribe

router = APIRouter(prefix="/sse", tags=["sse"])


async def event_stream(user_id: str):
    messages = []
    
    async def callback(message):
        messages.append(message)
    
    await subscribe(user_id, callback)
    
    try:
        while True:
            while messages:
                message = messages.pop(0)
                yield f"data: {message}\n\n"
            await asyncio.sleep(0.1)
    finally:
        await unsubscribe(user_id, callback)


@router.get("/notifications")
async def stream_notifications(current_user=Depends(get_current_user)):
    return StreamingResponse(
        event_stream(str(current_user.id)),
        media_type="text/event-stream"
    )
