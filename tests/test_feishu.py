# -*- coding: utf-8 -*-
"""飞书通知单测：webhook 缺失时静默跳过；发送失败不影响主流程；payload 结构正确。"""
import asyncio

import pytest

from src.services import feishu_notify as fn


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


def _patch_httpx(monkeypatch, records, exc=None):
    """拦截 httpx.AsyncClient.post，记录 payload，可模拟异常"""
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            records.append({"url": url, "json": json})
            if exc:
                raise exc
            return _FakeResponse()

    monkeypatch.setattr(fn.httpx, "AsyncClient", _Client)


def test_send_message_builds_card_payload(monkeypatch):
    records = []
    _patch_httpx(monkeypatch, records)
    asyncio.run(fn.send_feishu_message("https://open.feishu.cn/hook/x", "内容", "标题"))
    assert len(records) == 1
    payload = records[0]["json"]
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["title"]["content"] == "标题"
    assert "内容" in payload["card"]["elements"][0]["text"]["content"]


def test_send_message_swallows_network_error(monkeypatch):
    records = []
    _patch_httpx(monkeypatch, records, exc=ConnectionError("网络不通"))
    asyncio.run(fn.send_feishu_message("https://x", "内容"))  # 不抛错即通过
    assert len(records) == 1


def test_send_message_empty_url_noop(monkeypatch):
    records = []
    _patch_httpx(monkeypatch, records)
    asyncio.run(fn.send_feishu_message("", "内容"))
    assert records == []


def test_notify_without_webhook_config(monkeypatch):
    """FEISHU_WEBHOOK_URL 未配置时全部静默跳过"""
    monkeypatch.setattr(fn.settings, "FEISHU_WEBHOOK_URL", None)
    records = []
    _patch_httpx(monkeypatch, records)
    fn.notify_interview_scheduled("张三", "Python工程师", "2026-10-01 10:00", "李面试官")
    fn.notify_workflow_completed("张三", "Python工程师", "推荐录用", 88)
    asyncio.run(fn.notify_interview_scheduled_async("张三", "岗位", "时间", "面试官"))
    asyncio.run(fn.notify_workflow_completed_async("张三", "岗位", "录用", 90))
    assert records == []


def test_notify_interview_scheduled_content(monkeypatch):
    monkeypatch.setattr(fn.settings, "FEISHU_WEBHOOK_URL", "https://open.feishu.cn/hook/test")
    records = []
    _patch_httpx(monkeypatch, records)
    fn.notify_interview_scheduled("张三", "Python工程师", "2026-10-01 10:00", "李面试官")
    assert len(records) == 1
    content = records[0]["json"]["card"]["elements"][0]["text"]["content"]
    assert "张三" in content and "Python工程师" in content and "李面试官" in content


def test_notify_workflow_completed_content(monkeypatch):
    monkeypatch.setattr(fn.settings, "FEISHU_WEBHOOK_URL", "https://open.feishu.cn/hook/test")
    records = []
    _patch_httpx(monkeypatch, records)
    fn.notify_workflow_completed("李四", "Go工程师", "推荐录用", 92)
    content = records[0]["json"]["card"]["elements"][0]["text"]["content"]
    assert "李四" in content and "推荐录用" in content and "92" in content


def test_send_sync_in_running_loop_schedules_task(monkeypatch):
    """事件循环运行中调用同步入口 → ensure_future 不阻塞"""
    monkeypatch.setattr(fn.settings, "FEISHU_WEBHOOK_URL", "https://x")
    records = []
    _patch_httpx(monkeypatch, records)

    async def _main():
        fn._send_sync("https://x", "运行中发送", "标题")  # 不应死锁
        await asyncio.sleep(0.05)  # 给 ensure_future 的任务执行机会

    asyncio.run(_main())
    assert len(records) == 1


def test_send_sync_without_loop_runs_directly(monkeypatch):
    """无事件循环时同步入口直接 run_until_complete"""
    monkeypatch.setattr(fn.settings, "FEISHU_WEBHOOK_URL", "https://x")
    records = []
    _patch_httpx(monkeypatch, records)
    fn._send_sync("https://x", "无循环发送", "标题")
    assert len(records) == 1
