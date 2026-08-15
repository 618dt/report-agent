"""
    chat.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    对话接口

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.constants import BizCode
from app.logic.chat import (
    logic_cancel_run,
    logic_chat_stream,
    logic_subscribe_run_stream,
)
from app.schemas.chat import ChatRequest
from app.utils.auth import get_current_user_id, login
from app.utils.log import logger
from app.utils.response import error, success

router = APIRouter(prefix="/chat", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("/stream")
@login
async def chat_stream(
    body: ChatRequest,
    request: Request,
):
    """## Unified Chat Stream (SSE)

        POST '/api/chat/stream'

    启动后台 run 并以订阅者身份返回 SSE（客户端断开不取消任务）。

    统一的对话流式接口，根据参数自动切换模式：
    - 传 `query`（不传 approved/response）→ 新消息模式
    - 传 `approved` 或 `response`（不传 query）→ HITL 恢复模式
    - query 与恢复字段同时传 → 报错

    Errors: `30004`, `40001`

    ---
    """
    user_id = get_current_user_id(request)
    has_query = bool(body.query and body.query.strip())
    has_approved = body.approved is not None
    has_response = body.response is not None
    has_resume = has_approved or has_response

    if has_query and has_resume:
        return error(
            code=BizCode.PARAM_ERROR,
            message="query 与 approved/response 不能同时提供，请选择新消息模式或恢复模式",
            request=request,
        )
    if not has_query and not has_resume:
        return error(
            code=BizCode.PARAM_ERROR,
            message="query 与 approved/response 必须至少提供一个",
            request=request,
        )
    if has_resume and not (body.conversation_id and body.conversation_id.strip()):
        return error(
            code=BizCode.PARAM_ERROR,
            message="HITL 恢复模式下 conversation_id 不能为空",
            request=request,
        )

    response_dict = None
    if body.response is not None:
        response_dict = body.response.model_dump()

    async def event_generator():
        try:
            async for sse_event in logic_chat_stream(
                query=body.query.strip() if has_query else None,
                conversation_id=(body.conversation_id or "").strip(),
                user_id=user_id,
                approved=body.approved,
                run_id=body.run_id,
                response=response_dict,
                deep_thinking=bool(body.deep_thinking),
                plan_mode=bool(body.plan_mode),
            ):
                yield sse_event
        except Exception as e:
            logger.exception({
                "msg": "sse_stream_error",
                "conversation_id": body.conversation_id,
                "error": str(e),
            })
            yield (
                'data: {"type": "error", '
                f'"message": "Internal stream error: {str(e)}"'
                '}\n\n'
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/runs/{run_id}/stream")
@login
async def subscribe_run_stream(
    run_id: str,
    request: Request,
    after_seq: int = Query(default=-1, description="仅返回 seq 大于该值的事件"),
):
    """## Subscribe Run Stream (SSE)

        GET '/api/chat/runs/{run_id}/stream?after_seq=-1'

    续订已有 run 的事件流（刷新/重连）。断开连接不会取消后台任务。

    Errors: `30004`, `40001`

    ---
    """
    user_id = get_current_user_id(request)

    async def event_generator():
        try:
            async for sse_event in logic_subscribe_run_stream(
                run_id=run_id,
                user_id=user_id,
                after_seq=after_seq,
            ):
                yield sse_event
        except Exception as e:
            logger.exception({
                "msg": "sse_subscribe_error",
                "run_id": run_id,
                "error": str(e),
            })
            yield (
                'data: {"type": "error", '
                f'"message": "Internal stream error: {str(e)}"'
                '}\n\n'
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/runs/{run_id}/cancel")
@login
async def cancel_run(
    run_id: str,
    request: Request,
):
    """## Cancel Run

        POST '/api/chat/runs/{run_id}/cancel'

    显式取消 run（刷新/关页不会触发此逻辑）。

    Errors: `30004`, `40001`

    ---
    """
    user_id = get_current_user_id(request)
    result = await logic_cancel_run(run_id=run_id, user_id=user_id)
    return success(data=result, request=request)


# ---------------------------------------------------------------------------
# 向后兼容端点（deprecated，建议迁移至 /api/chat/stream）
# ---------------------------------------------------------------------------


@router.post("/send-message")
@login
async def send_message(
    body: ChatRequest,
    request: Request,
):
    """## Send Chat Message (SSE Stream) — Deprecated

        POST '/api/chat/send-message'

    .. deprecated::
        请迁移至 POST /api/chat/stream，传入 query 字段即可。
        此端点将委托给统一接口处理。

    ---
    """
    user_id = get_current_user_id(request)

    if not body.query or not body.query.strip():
        return error(
            code=BizCode.PARAM_ERROR,
            message="query 不能为空",
            request=request,
        )

    async def event_generator():
        try:
            async for sse_event in logic_chat_stream(
                query=body.query.strip(),
                conversation_id=(body.conversation_id or "").strip(),
                user_id=user_id,
                deep_thinking=bool(body.deep_thinking),
                plan_mode=bool(body.plan_mode),
            ):
                yield sse_event
        except Exception as e:
            logger.exception({
                "msg": "sse_stream_error",
                "conversation_id": body.conversation_id,
                "error": str(e),
            })
            yield (
                'data: {"type": "error", '
                f'"message": "Internal stream error: {str(e)}"'
                '}\n\n'
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
