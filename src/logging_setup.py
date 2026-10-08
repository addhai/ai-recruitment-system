"""结构化日志配置。

此前全项目只有 resume_cleaner 用了 logging，其余 18 处都是裸 print，
LLM 调用失败时仅打印一行文本——线上排查时无法还原"当时调了什么、慢在哪、
花了多少"。这里统一为 JSON 行输出到 stdout，方便被日志采集系统解析。

**日志里不放 prompt 全文与模型输出全文**：简历里含手机号等 PII，
虽然 OutputGuard 已对接口响应脱敏，日志留存仍应最小化。
需要排查具体内容时，去 llm_call_logs 看调用元数据与 candidate_id 关联。
"""
import json
import logging
import sys
from datetime import datetime, timezone

_CONFIGURED = False

# 这些 logger 会刷屏（每页打印若干行），调到 WARNING 之上
_NOISY = ("httpx", "httpcore", "urllib3", "chromadb", "langchain", "openai")


class JsonFormatter(logging.Formatter):
    """把 LogRecord 渲染成单行 JSON，附带 logger 自动注入的上下文字段。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
                     .astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # logger.info("x", extra={"call_site": ...}) 注入的字段原样带出
        for key in ("call_site", "model", "input_tokens", "output_tokens",
                    "cost_usd", "latency_ms", "status", "candidate_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)[:800]
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """幂等初始化；重复调用不会叠加 handler"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)