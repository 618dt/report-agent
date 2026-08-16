"""
    finalize.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    产物提交：供 commit 节点与流结束兜底共用，不依赖模型选工具。

"""
from __future__ import annotations

from typing import Any

from app.agent.artifacts.protocol import get_protocol
from app.models.chat.chat_model import ChatRunEvent
from app.utils.log import logger
from app.utils.text_helper import count_chinese_chars


async def artifact_already_emitted(run_id: str) -> bool:
    """该 run 是否已有终态 artifact 事件。"""
    if not run_id:
        return False
    doc = await ChatRunEvent.a_p_col.find_one({
        "run_id": run_id,
        "type": ChatRunEvent.TypeField.ARTIFACT,
        "is_deleted": 0,
    })
    return doc is not None


async def finalize_artifact(
    *,
    kind: str,
    markdown: str,
    meta: dict[str, Any] | None,
    conversation_id: str,
    run_id: str,
    publish: bool = True,
) -> list[str]:
    """将产物正文落为终态 artifact、标记草稿 ready、收口撰写步骤。

    Arguments:
        kind -- 产物类型（如 report）
        markdown -- 完整正文
        meta -- pending_artifact（title/topic/tool_call_id）
        conversation_id -- 会话 ID
        run_id -- ChatRun ID
        publish -- True 时同时写入本地 hub / Redis（图节点路径）

    Returns:
        list[str] -- 已生成的 SSE（artifact / plan）；未提交时空列表
    """
    body = (markdown or "").strip()
    if not body or not run_id:
        return []
    if await artifact_already_emitted(run_id):
        logger.info({
            "msg": "artifact_finalize_skipped_exists",
            "run_id": run_id,
            "kind": kind,
        })
        return []

    protocol = get_protocol(kind) or get_protocol("report")
    meta = meta if isinstance(meta, dict) else {}
    tool_call_id = str(
        meta.get("tool_call_id") or f"{kind}_{run_id}",
    )
    args = {
        "title": str(meta.get("title") or ""),
        "topic": str(meta.get("topic") or ""),
        "markdown": body,
    }

    from app.logic.chat import (
        _auto_plan_step_for_report,
        _emit_report_artifact,
        _parse_sse_payload,
        _persist_partial_report,
        _publish_sse_payload,
    )

    events: list[str] = []
    sse = await _emit_report_artifact(
        conversation_id=conversation_id,
        run_id=run_id,
        tool_call_id=tool_call_id,
        args=args,
    )
    if not sse:
        return []
    events.append(sse)

    last_seq = 0
    payload = _parse_sse_payload(sse)
    if isinstance(payload, dict):
        try:
            last_seq = int(payload.get("seq") or 0)
        except (TypeError, ValueError):
            last_seq = 0
        if publish:
            await _publish_sse_payload(payload)

    await _persist_partial_report(run_id, {
        "tool_call_id": tool_call_id,
        "title": args["title"],
        "topic": args["topic"],
        "markdown": body,
        "status": "ready",
        "last_seq": last_seq,
    })

    note = (protocol.plan_complete_note if protocol else "已提交")
    plan_sse = await _auto_plan_step_for_report(
        conversation_id=conversation_id,
        run_id=run_id,
        status="completed",
        note=note,
    )
    if plan_sse:
        events.append(plan_sse)
        if publish:
            plan_payload = _parse_sse_payload(plan_sse)
            if plan_payload:
                await _publish_sse_payload(plan_payload)

    logger.info({
        "msg": "artifact_finalize_ok",
        "run_id": run_id,
        "kind": kind or "report",
        "zh_chars": count_chinese_chars(body),
        "tool_call_id": tool_call_id,
    })
    return events
