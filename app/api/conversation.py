"""
    conversation.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    对话接口

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.constants import BizCode
from app.logic.conversation import (
    logic_delete_conversation,
    logic_get_active_run,
    logic_get_conversation_list,
    logic_get_interrupted_run,
    logic_get_message_list,
    logic_get_run_events,
    logic_update_conversation_title,
)
from app.schemas.chat import UpdateConversationTitleRequest
from app.utils.response import error, paginated, success
from app.utils.auth import get_current_user_id, login

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("/list")
@login
async def get_conversation_list(
    request: Request,
    start: int = Query(default=0, ge=0, description="起始索引，从 0 开始"),
    end: int = Query(default=20, ge=0, description="结束索引，不包含"),
):
    """
    ## Get Conversation List

        GET '/api/conversations/list'

    Params:
    * `start` (int) *optional* - 起始索引，从 0 开始，默认 0
    * `end` (int) *optional* - 结束索引（不包含），默认 20

    Returns:
    * `items` (list) - 对话列表，按更新时间倒序
    * `total` (int) - 总数

    Errors: `40001`

    ---
    """
    if start < 0 or end < 0 or end <= start:
        return error(
            code=BizCode.PARAM_ERROR,
            message="start 必须 >= 0 且 end 必须 > start",
            request=request,
        )

    # 从request中获取user_id
    user_id = get_current_user_id(request)

    result = await logic_get_conversation_list(
        owner_id=user_id,
        start=start,
        end=end,
    )
    return paginated(
        items=result['items'],
        total=result['total'],
        request=request,
    )


@router.delete("/{conversation_id}")
@login
async def delete_conversation(
    conversation_id: str,
    request: Request,
):
    """
    ## Delete Conversation

        DELETE '/api/conversations/{conversation_id}'

    Params:
    * `conversation_id` (str) - 对话 ID

    Returns:
    * `None`

    Errors: `30004`, `40001`

    ---
    """
    # 从request中获取user_id
    user_id = get_current_user_id(request)

    await logic_delete_conversation(
        conversation_id=conversation_id,
        owner_id=user_id,
    )
    return success(data=None, request=request)


@router.put("/{conversation_id}")
@login
async def update_conversation_title(
    conversation_id: str,
    body: UpdateConversationTitleRequest,
    request: Request,
):
    """
    ## Update Conversation Title

        PUT '/api/conversations/{conversation_id}'

    Params:
    * `conversation_id` (str) - 对话 ID
    * `title` (str) - 新标题

    Returns:
    * `conversation` (dict) - 更新后的对话信息

    Errors: `30004`, `40001`

    ---
    """
    if not body.title:
        return error(
            code=BizCode.PARAM_ERROR,
            message="title 不能为空",
            request=request,
        )

    # 从request中获取user_id
    user_id = get_current_user_id(request)

    conversation = await logic_update_conversation_title(
        conversation_id=conversation_id,
        owner_id=user_id,
        title=body.title,
    )
    return success(data=conversation, request=request)


@router.get("/{conversation_id}/messages")
@login
async def get_message_list(
    conversation_id: str,
    request: Request,
    start: int = Query(default=0, ge=0, description="起始索引，从 0 开始（最新消息）"),
    end: int = Query(default=10, ge=0, description="结束索引，不包含"),
):
    """
    ## Get Message List

        GET '/api/conversations/{conversation_id}/messages'

    查询对话历史消息列表，按创建时间倒序排列（最新消息在前）。
    前端首次加载传入 `start=0&end=10` 获取最新 10 条；
    向上翻页时传入 `start=10&end=20` 获取更早的 10 条，以此类推。

    Params:
    * `start` (int) *optional* - 起始索引，从 0 开始，默认 0
    * `end` (int) *optional* - 结束索引（不包含），默认 10

    Returns:
    * `items` (list) - 消息列表，按创建时间倒序
    * `total` (int) - 该会话的消息总数

    Errors: `30004`, `40001`

    ---
    """
    if start < 0 or end < 0 or end <= start:
        return error(
            code=BizCode.PARAM_ERROR,
            message="start 必须 >= 0 且 end 必须 > start",
            request=request,
        )

    user_id = get_current_user_id(request)

    result = await logic_get_message_list(
        conversation_id=conversation_id,
        owner_id=user_id,
        start=start,
        end=end,
    )
    return paginated(
        items=result['items'],
        total=result['total'],
        request=request,
    )


@router.get("/{conversation_id}/runs/interrupted")
@login
async def get_interrupted_run(
    conversation_id: str,
    request: Request,
):
    """## Get Interrupted Run

        GET '/api/conversations/{conversation_id}/runs/interrupted'

    查询当前会话最近一个 interrupted 的 ChatRun 及 interrupt payload，
    用于刷新后恢复 HITL 确认面板。无中断 run 时 data 为 null。

    推荐迁移至 ``GET .../runs/active``（同时支持 running）。

    Errors: ``30004``, ``40001``

    ---
    """
    user_id = get_current_user_id(request)
    result = await logic_get_interrupted_run(
        conversation_id=conversation_id,
        owner_id=user_id,
    )
    return success(data=result, request=request)


@router.get("/{conversation_id}/runs/active")
@login
async def get_active_run(
    conversation_id: str,
    request: Request,
):
    """## Get Active Run

        GET '/api/conversations/{conversation_id}/runs/active'

    查询当前会话最近一个 ``running`` 或 ``interrupted`` 的 ChatRun，
    用于刷新后恢复确认面板或续订流式输出。无活跃 run 时 data 为 null。

    Errors: ``30004``, ``40001``

    ---
    """
    user_id = get_current_user_id(request)
    result = await logic_get_active_run(
        conversation_id=conversation_id,
        owner_id=user_id,
    )
    return success(data=result, request=request)


@router.get("/{conversation_id}/runs/events")
@login
async def get_run_events(
    conversation_id: str,
    request: Request,
    run_ids: str = Query(default='', description="逗号分隔的 run_id 列表，例如 run_1,run_2"),
):
    """## Get Run Events

        GET '/api/conversations/{conversation_id}/runs/events'

    根据 run_id 列表批量查询 Agent 执行事件（工具调用、工具结果、中断等）。
    返回按 run_id + seq 排序的事件列表，并附带各 run 的 token 用量。

    Params:
    * ``run_ids`` (str) — 逗号分隔的 run_id 列表，如 ``run_1,run_2``

    Returns:
    * ``items`` (list) — 事件列表，按 run_id + seq 升序
    * ``total`` (int) — 总数
    * ``runs`` (list) — ``[{_id, usage, status}, ...]``，用于历史回显 token

    Errors: ``30004``, ``40001``

    ---
    """
    if not run_ids or not run_ids.strip():
        return success(
            data={"items": [], "total": 0, "runs": []},
            request=request,
        )

    user_id = get_current_user_id(request)
    run_id_list = [rid.strip() for rid in run_ids.split(',') if rid.strip()]

    result = await logic_get_run_events(
        conversation_id=conversation_id,
        owner_id=user_id,
        run_ids=run_id_list,
    )
    return success(data=result, request=request)
