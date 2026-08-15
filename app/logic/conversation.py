"""
    conversation.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    对话业务逻辑

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.constants import BizCode
from app.models.chat.chat_model import ChatRun, ChatRunEvent, Conversation, Message
from app.utils.exception_handler import AppException
from app.utils.log import logger
from app.utils.time_helper import datetime2timestamp


async def logic_get_interrupted_run(
    conversation_id: str,
    owner_id: str,
) -> dict | None:
    """查询会话中最近一个 interrupted 的 ChatRun（兼容旧接口）。"""
    active = await logic_get_active_run(conversation_id, owner_id)
    if active and active.get("status") == ChatRun.StatusField.INTERRUPTED:
        return active
    return None


async def logic_get_active_run(
    conversation_id: str,
    owner_id: str,
) -> dict | None:
    """查询会话中最近一个 running 或 interrupted 的 ChatRun。

    用于刷新后恢复 HITL 面板或续订 running 流。
    读写均走 primary。若发现幽灵 running 会尝试清理。

    Returns:
        dict | None -- {
            run_id, status, interrupt, events, partial_content,
            partial_report, last_seq, ...
        } 或 None
    """
    # 延迟导入，避免 conversation ↔ chat 循环依赖
    from app.logic.chat import _maybe_fail_ghost_running

    conv_doc = await Conversation.a_p_col.find_one({
        "_id": conversation_id,
        "owner_id": owner_id,
        "is_deleted": 0,
    })
    if conv_doc is None:
        raise AppException(
            message="会话不存在或已删除",
            code=BizCode.NOT_FOUND,
            status_code=404,
        )

    run_doc = await ChatRun.a_p_col.find_one({
        "conversation_id": conversation_id,
        "owner_id": owner_id,
        "status": {
            "$in": [
                ChatRun.StatusField.RUNNING,
                ChatRun.StatusField.INTERRUPTED,
            ],
        },
        "is_deleted": 0,
    }, sort=[("update_time", -1)])

    if not run_doc:
        return None

    if run_doc.get("status") == ChatRun.StatusField.RUNNING:
        run_doc = await _maybe_fail_ghost_running(run_doc)
        if run_doc.get("status") != ChatRun.StatusField.RUNNING:
            # 幽灵清理后若非 running，再查是否仍有 interrupted
            run_doc = await ChatRun.a_p_col.find_one({
                "conversation_id": conversation_id,
                "owner_id": owner_id,
                "status": ChatRun.StatusField.INTERRUPTED,
                "is_deleted": 0,
            }, sort=[("update_time", -1)])
            if not run_doc:
                return None

    run_id = run_doc["_id"]
    interrupt_evt = await ChatRunEvent.a_p_col.find_one({
        "conversation_id": conversation_id,
        "run_id": run_id,
        "type": ChatRunEvent.TypeField.INTERRUPT,
        "is_deleted": 0,
    }, sort=[("seq", -1)])

    events_cursor = (
        ChatRunEvent.a_p_col.find({
            "conversation_id": conversation_id,
            "run_id": run_id,
            "is_deleted": 0,
        }).sort([("seq", 1)])
    )
    event_docs = await events_cursor.to_list(length=200)

    interrupt_payload = {}
    if interrupt_evt:
        interrupt_payload = interrupt_evt.get("payload") or {}

    logger.info({
        "msg": "get_active_run",
        "conversation_id": conversation_id,
        "run_id": run_id,
        "status": run_doc.get("status"),
        "reason": (
            interrupt_payload.get("reason")
            if isinstance(interrupt_payload, dict) else None
        ),
    })

    return {
        "run_id": run_id,
        "status": run_doc.get("status"),
        "interrupt": interrupt_payload if run_doc.get("status") == ChatRun.StatusField.INTERRUPTED else None,
        "events": [_event_to_response(doc) for doc in event_docs],
        "user_message_id": run_doc.get("user_message_id", ""),
        "usage": run_doc.get("usage") or None,
        "partial_content": run_doc.get("partial_content") or "",
        "partial_report": run_doc.get("partial_report") or None,
        "last_seq": int(run_doc.get("last_seq") or 0),
        "plan": run_doc.get("plan"),
        "plan_mode": bool(run_doc.get("plan_mode", False)),
    }


def _doc_to_response(doc: dict) -> dict:
    """将 MongoDB 文档转换为 API 响应字典

    Arguments:
        doc {dict} -- MongoDB 文档

    Returns:
        dict -- 可序列化的响应字典
    """
    return {
        '_id': doc['_id'],
        'title': doc.get('title', ''),
        'owner_id': doc.get('owner_id', ''),
        'last_msg_id': doc.get('last_msg_id', ''),
        'last_msg_content': doc.get('last_msg_content', ''),
        'create_time': datetime2timestamp(doc['create_time']),
        'update_time': datetime2timestamp(doc['update_time']),
    }


async def logic_create_conversation(
    owner_id: str,
    title: str = '',
) -> dict:
    """创建对话

    Arguments:
        owner_id {str} -- 所有者用户 ID
        title {str} -- 对话标题，默认为空

    Returns:
        dict -- 新创建的对话信息
    """
    conversation = Conversation(
        title=title,
        owner_id=owner_id,
    )
    await Conversation.a_p_col.insert_one(conversation.to_dict())

    logger.info({
        'msg': 'create_conversation',
        'conversation_id': conversation._id,
        'owner_id': owner_id,
    })

    return _doc_to_response(conversation.to_dict())


async def logic_get_conversation_list(
    owner_id: str,
    start: int = 0,
    end: int = 20,
) -> dict:
    """查询对话列表（按更新时间倒序）

    Arguments:
        owner_id {str} -- 所有者用户 ID
        start {int} -- 起始索引，从 0 开始
        end {int} -- 结束索引（不包含）

    Returns:
        dict -- {'items': list[dict], 'total': int}
    """
    query = {'owner_id': owner_id, 'is_deleted': 0}

    total = await Conversation.a_s_col.count_documents(query)
    limit = end - start

    cursor = (
        Conversation.a_s_col.find(query)
        .sort('update_time', -1)
        .skip(start)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)

    items = [_doc_to_response(doc) for doc in docs]

    return {'items': items, 'total': total}


async def logic_delete_conversation(
    conversation_id: str,
    owner_id: str,
) -> bool:
    """删除对话（逻辑删除）

    Arguments:
        conversation_id {str} -- 对话 ID
        owner_id {str} -- 所有者用户 ID

    Returns:
        bool -- 删除成功返回 True

    Raises:
        AppException: 对话不存在或已删除时抛出
    """
    now = datetime.now(timezone.utc)
    result = await Conversation.a_p_col.update_one(
        {
            '_id': conversation_id,
            'owner_id': owner_id,
            'is_deleted': 0,
        },
        {'$set': {'is_deleted': 1, 'update_time': now}},
    )

    if result.matched_count == 0:
        raise AppException(
            message="对话不存在或已删除",
            code=BizCode.NOT_FOUND,
            status_code=404,
        )

    logger.info({
        'msg': 'delete_conversation',
        'conversation_id': conversation_id,
        'owner_id': owner_id,
    })

    return True


def _message_to_response(doc: dict) -> dict:
    """将消息 MongoDB 文档转换为 API 响应字典

    Arguments:
        doc {dict} -- MongoDB 文档

    Returns:
        dict -- 可序列化的响应字典
    """
    return {
        '_id': doc['_id'],
        'conversation_id': doc.get('conversation_id', ''),
        'sender_id': doc.get('sender_id', ''),
        'receiver_id': doc.get('receiver_id', ''),
        'msg_type': doc.get('msg_type', 1),
        'content': doc.get('content', ''),
        'reply_msg_id': doc.get('reply_msg_id', ''),
        'reply_summary': doc.get('reply_summary', ''),
        'status': doc.get('status', 1),
        'run_id': doc.get('run_id', ''),
        'create_time': datetime2timestamp(doc['create_time']),
        'update_time': datetime2timestamp(doc['update_time']),
    }


async def logic_get_message_list(
    conversation_id: str,
    owner_id: str,
    start: int = 0,
    end: int = 10,
) -> dict:
    """查询对话历史消息列表（按创建时间倒序，最新消息在前）

    Arguments:
        conversation_id {str} -- 对话 ID
        owner_id {str} -- 所有者用户 ID
        start {int} -- 起始索引，从 0 开始，默认 0（最新消息）
        end {int} -- 结束索引（不包含），默认 10

    Returns:
        dict -- {'items': list[dict], 'total': int}

    Raises:
        AppException: 对话不存在或已删除时抛出
    """
    # 验证会话是否存在且属于当前用户
    conv_doc = await Conversation.a_s_col.find_one({
        "_id": conversation_id,
        "owner_id": owner_id,
        "is_deleted": 0,
    })
    if conv_doc is None:
        raise AppException(
            message="会话不存在或已删除",
            code=BizCode.NOT_FOUND,
            status_code=404,
        )

    query = {'conversation_id': conversation_id, 'is_deleted': 0}

    total = await Message.a_s_col.count_documents(query)
    limit = end - start

    cursor = (
        Message.a_s_col.find(query)
        .sort('create_time', -1)
        .skip(start)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)

    items = [_message_to_response(doc) for doc in docs]

    return {'items': items, 'total': total}


def _event_to_response(doc: dict) -> dict:
    """将 ChatRunEvent MongoDB 文档转换为 API 响应字典

    Arguments:
        doc {dict} -- MongoDB 文档

    Returns:
        dict -- 可序列化的响应字典
    """
    return {
        '_id': doc['_id'],
        'conversation_id': doc.get('conversation_id', ''),
        'run_id': doc.get('run_id', ''),
        'parent_message_id': doc.get('parent_message_id', ''),
        'seq': doc.get('seq', 0),
        'type': doc.get('type', ''),
        'payload': doc.get('payload', {}),
        'visible': doc.get('visible', True),
        'create_time': datetime2timestamp(doc['create_time']),
        'update_time': datetime2timestamp(doc['update_time']),
    }


async def logic_get_run_events(
    conversation_id: str,
    owner_id: str,
    run_ids: list[str],
) -> dict:
    """批量查询 run 事件列表

    根据 run_ids 列表查询对应的 chat_run_event 记录，
    按 run_id + seq 排序。

    Arguments:
        conversation_id {str} -- 对话 ID
        owner_id {str} -- 所有者用户 ID
        run_ids {list[str]} -- run ID 列表

    Returns:
        dict -- {'items': list[dict], 'total': int}

    Raises:
        AppException: 会话不存在或已删除时抛出
    """
    # 验证会话是否存在且属于当前用户
    conv_doc = await Conversation.a_s_col.find_one({
        "_id": conversation_id,
        "owner_id": owner_id,
        "is_deleted": 0,
    })
    if conv_doc is None:
        raise AppException(
            message="会话不存在或已删除",
            code=BizCode.NOT_FOUND,
            status_code=404,
        )

    if not run_ids:
        return {'items': [], 'total': 0}

    query = {
        'conversation_id': conversation_id,
        'run_id': {'$in': run_ids},
        'is_deleted': 0,
    }

    total = await ChatRunEvent.a_s_col.count_documents(query)

    cursor = (
        ChatRunEvent.a_s_col.find(query)
        .sort([('run_id', 1), ('seq', 1)])
    )
    docs = await cursor.to_list(length=500)

    items = [_event_to_response(doc) for doc in docs]

    # 附带各 run 的 token 用量，便于历史消息展示
    runs_cursor = ChatRun.a_s_col.find(
        {
            '_id': {'$in': run_ids},
            'conversation_id': conversation_id,
            'is_deleted': 0,
        },
        {'_id': 1, 'usage': 1, 'status': 1},
    )
    run_docs = await runs_cursor.to_list(length=len(run_ids) or 1)
    runs = [
        {
            '_id': doc['_id'],
            'usage': doc.get('usage') or None,
            'status': doc.get('status', ''),
        }
        for doc in run_docs
    ]

    logger.info({
        'msg': 'get_run_events',
        'conversation_id': conversation_id,
        'run_ids_count': len(run_ids),
        'events_count': len(items),
    })

    return {'items': items, 'total': total, 'runs': runs}


async def logic_update_conversation_title(
    conversation_id: str,
    owner_id: str,
    title: str,
) -> dict:
    """修改对话标题

    Arguments:
        conversation_id {str} -- 对话 ID
        owner_id {str} -- 所有者用户 ID
        title {str} -- 新标题

    Returns:
        dict -- 更新后的对话信息

    Raises:
        AppException: 对话不存在或已删除时抛出
    """
    now = datetime.now(timezone.utc)
    result = await Conversation.a_p_col.find_one_and_update(
        {
            '_id': conversation_id,
            'owner_id': owner_id,
            'is_deleted': 0,
        },
        {'$set': {'title': title, 'update_time': now}},
        return_document=ReturnDocument.AFTER,
    )

    if result is None:
        raise AppException(
            message="对话不存在或已删除",
            code=BizCode.NOT_FOUND,
            status_code=404,
        )

    logger.info({
        'msg': 'update_conversation_title',
        'conversation_id': conversation_id,
        'owner_id': owner_id,
        'title': title,
    })

    return _doc_to_response(result)
