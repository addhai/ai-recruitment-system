"""模型接入配置接口。

本项目不是模型中转站，不预置固定供应商接入：模型名、API Key、Base URL、
单价都由使用者在界面上填写，后端据此切换，无需重启。

权限：仅管理员——模型配置涉及账号密钥与成本口径。
密钥安全：接口永不回传明文，只回掩码预览。
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.api.auth import require_admin
from src.services import llm_config

router = APIRouter(prefix="/llm-config", tags=["llm-config"])


class LLMConfigUpdate(BaseModel):
    """全部字段可选：只传需要改的项。

    api_key 语义：不传=不修改；传空字符串=清空（回退 .env）。
    """
    model: Optional[str] = Field(None, max_length=100)
    base_url: Optional[str] = Field(None, max_length=200)
    api_key: Optional[str] = None
    input_price: Optional[float] = Field(None, ge=0)
    output_price: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = Field(None, max_length=8)
    peak_multiplier: Optional[float] = Field(None, ge=1)
    temperature: Optional[float] = Field(None, ge=0, le=2)
    timeout_seconds: Optional[int] = Field(None, ge=1, le=600)


@router.get("")
def get_config(current_user=Depends(require_admin)) -> Dict[str, Any]:
    """当前生效配置（密钥以掩码返回）+ 是否处于高峰计价时段。"""
    return llm_config.masked_view()


@router.put("")
def update_config(body: LLMConfigUpdate, current_user=Depends(require_admin)) -> Dict[str, Any]:
    """保存配置并立即生效（清空客户端缓存，无需重启）。"""
    llm_config.save_config(
        model=body.model, base_url=body.base_url, api_key=body.api_key,
        input_price=body.input_price, output_price=body.output_price,
        currency=body.currency, peak_multiplier=body.peak_multiplier,
        temperature=body.temperature, timeout_seconds=body.timeout_seconds,
        updated_by=current_user.id,
    )
    return llm_config.masked_view()


@router.get("/presets")
def get_presets(current_user=Depends(require_admin)) -> Dict[str, Any]:
    """内置供应商预设（当前为 DeepSeek 官方定价），方便一键填入。"""
    return llm_config.list_presets()


@router.post("/test")
async def test_config(current_user=Depends(require_admin)) -> Dict[str, Any]:
    """用当前配置发一次最小调用，验证模型名/密钥/Base URL 是否可用。

    配置界面的关键一环：填错了要当场知道，而不是等到跑候选人评估才失败。
    """
    return await llm_config.test_connection()


@router.post("/invalidate")
def invalidate(current_user=Depends(require_admin)) -> Dict[str, str]:
    """手动清空客户端缓存（改过 .env 后不想重启服务时使用）。"""
    llm_config.invalidate_client_cache()
    return {"message": "客户端缓存已清空，下次调用将使用最新配置"}