import asyncio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from src.api.auth import get_current_user, require_all_authenticated
from src.sse.notification import subscribe, unsubscribe

router = APIRouter(prefix="/sse", tags=["sse"])


async def event_stream(user_id: str):
    messages = []

    # 注意必须是普通函数：notification.broadcast 同步调用 callback，
    # 若声明为 async def 会只创建协程不执行，导致事件永远推不出去
    def callback(message):
        messages.append(message)

    await subscribe(user_id, callback)
    
    try:
        # 连接建立即发首事件：客户端可确认链路已通，也避免空流导致的长连接无响应
        yield 'data: {"type": "connected"}\n\n'
        while True:
            while messages:
                message = messages.pop(0)
                yield f"data: {message}\n\n"
            await asyncio.sleep(0.1)
    finally:
        await unsubscribe(user_id, callback)


@router.get("/notifications")
async def stream_notifications(current_user=Depends(require_all_authenticated)):
    return StreamingResponse(
        event_stream(str(current_user.id)),
        media_type="text/event-stream"
    )
