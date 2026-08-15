"""
    chat.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    对话相关 API Schema

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateConversationRequest(BaseModel):
    """创建对话请求"""
    owner_id: str = Field(..., description="所有者用户 ID")
    title: str = Field(default='', description="对话标题")


class UpdateConversationTitleRequest(BaseModel):
    """修改对话标题请求"""
    title: str = Field(..., description="新标题")


class HitlResponse(BaseModel):
    """人机协作恢复响应

    action:
      - tool_approval: approve / deny
      - outline_confirm: confirm / revise / cancel
      - plan_confirm: confirm / revise / cancel
    """
    action: str = Field(
        ...,
        description="用户动作：confirm / revise / cancel / approve / deny",
    )
    payload: dict[str, Any] | None = Field(
        default=None,
        description="附加数据，如 chapters、steps、feedback",
    )


class ChatRequest(BaseModel):
    """统一聊天请求

    支持两种模式：
    1. 新消息模式：传 query，不传 approved / response
    2. 审批恢复模式：传 approved 或 response，不传 query

    两种字段至少提供一个。
    """
    query: str | None = Field(
        default=None, min_length=1, max_length=10000, description="用户问句（新消息时填写）",
    )
    conversation_id: str = Field(
        default='', description="会话 ID，新消息时为空则自动创建新会话",
    )
    approved: bool | None = Field(
        default=None, description="是否批准工具调用（恢复中断时填写，兼容旧协议）",
    )
    response: HitlResponse | None = Field(
        default=None, description="结构化 HITL 恢复响应（推荐）",
    )
    run_id: str | None = Field(
        default=None, description="恢复中断时传入的 run_id，不传则自动查找最近中断 run",
    )
    deep_thinking: bool = Field(
        default=False,
        description="是否开启深度思考（True=thinking max；False=关闭思考）",
    )
    plan_mode: bool = Field(
        default=False,
        description="是否开启 Plan 模式：先产出可编辑计划并确认，再执行任务",
    )


class ResumeRequest(BaseModel):
    """恢复中断对话请求（工具审批）

    .. deprecated::
        请使用 ChatRequest 的 approved / response 字段，通过 POST /api/chat/stream 调用。
        此接口保留仅用于向后兼容。
    """
    conversation_id: str = Field(..., min_length=1, description="会话 ID")
    approved: bool = Field(..., description="是否批准工具调用，false 则跳过工具执行")
