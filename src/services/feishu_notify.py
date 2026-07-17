import asyncio
import httpx
from src.config import settings


async def send_feishu_message(webhook_url: str, content: str, title: str = "通知"):
    """发送飞书机器人消息"""
    if not webhook_url:
        return
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}]
        }
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(webhook_url, json=payload)
    except Exception as e:
        # 飞书通知失败不应影响主流程
        print(f"[feishu_notify] 发送飞书消息失败: {e}")


def _get_event_loop():
    """获取事件循环，若不存在则新建"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
        if loop.is_running():
            return None
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _send_sync(webhook_url: str, content: str, title: str):
    """同步调用发送飞书消息（用于非 async 上下文）"""
    loop = _get_event_loop()
    if loop is None:
        # 当前已有事件循环在运行，创建任务但不阻塞
        asyncio.ensure_future(send_feishu_message(webhook_url, content, title))
        return
    loop.run_until_complete(send_feishu_message(webhook_url, content, title))


async def _send_async(webhook_url: str, content: str, title: str):
    """异步发送飞书消息"""
    await send_feishu_message(webhook_url, content, title)


def notify_interview_scheduled(candidate_name: str, position: str, time: str, interviewer: str):
    """面试安排通知"""
    if not settings.FEISHU_WEBHOOK_URL:
        return
    content = (
        f"**面试安排通知**\n"
        f"候选人：{candidate_name}\n"
        f"职位：{position}\n"
        f"时间：{time}\n"
        f"面试官：{interviewer}"
    )
    _send_sync(settings.FEISHU_WEBHOOK_URL, content, "面试安排通知")


def notify_workflow_completed(candidate_name: str, position: str, final_decision: str, overall_score):
    """工作流完成通知"""
    if not settings.FEISHU_WEBHOOK_URL:
        return
    content = (
        f"**AI评估完成通知**\n"
        f"候选人：{candidate_name}\n"
        f"职位：{position}\n"
        f"最终决策：{final_decision}\n"
        f"综合评分：{overall_score}"
    )
    _send_sync(settings.FEISHU_WEBHOOK_URL, content, "AI评估完成通知")


async def notify_interview_scheduled_async(candidate_name: str, position: str, time: str, interviewer: str):
    """异步面试安排通知"""
    if not settings.FEISHU_WEBHOOK_URL:
        return
    content = (
        f"**面试安排通知**\n"
        f"候选人：{candidate_name}\n"
        f"职位：{position}\n"
        f"时间：{time}\n"
        f"面试官：{interviewer}"
    )
    await send_feishu_message(settings.FEISHU_WEBHOOK_URL, content, "面试安排通知")


async def notify_workflow_completed_async(candidate_name: str, position: str, final_decision: str, overall_score):
    """异步工作流完成通知"""
    if not settings.FEISHU_WEBHOOK_URL:
        return
    content = (
        f"**AI评估完成通知**\n"
        f"候选人：{candidate_name}\n"
        f"职位：{position}\n"
        f"最终决策：{final_decision}\n"
        f"综合评分：{overall_score}"
    )
    await send_feishu_message(settings.FEISHU_WEBHOOK_URL, content, "AI评估完成通知")
