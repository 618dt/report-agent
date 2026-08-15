"""
    request_user_confirmation.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    人机协作确认工具：通过 LangGraph interrupt 暂停执行，等待用户结构化响应。

"""
from __future__ import annotations

from typing import Any

from langchain.tools import tool
from langgraph.types import interrupt

from app.utils.log import logger


@tool
def request_user_confirmation(
    title: str,
    topic: str,
    chapters: list[dict[str, Any]],
) -> dict[str, Any]:
    """向用户确认报告章节目录。生成报告前必须调用本工具，等待用户确认后再撰写。

    禁止用 Markdown 表格或清单在对话里展示目录；只能通过本工具的 chapters 参数传递，
    前端会渲染可编辑确认面板。

    Arguments:
        title -- 确认面板标题，例如「确认《新能源汽车》报告章节大纲」
        topic -- 报告主题/领域名称
        chapters -- 章节列表，每项含 id/title/description/selected

    Returns:
        dict -- 用户决策，形如
            {action: confirm|revise|cancel, payload: {chapters?, feedback?}}
    """
    normalized: list[dict[str, Any]] = []
    for i, ch in enumerate(chapters or []):
        if not isinstance(ch, dict):
            continue
        normalized.append({
            "id": str(ch.get("id") or str(i + 1)),
            "title": str(ch.get("title") or f"章节 {i + 1}"),
            "description": str(ch.get("description") or ""),
            "selected": bool(ch.get("selected", True)),
        })

    payload = {
        "reason": "outline_confirm",
        "title": title or "确认报告章节大纲",
        "schema": {
            "type": "outline_confirm",
            "topic": topic or "",
            "chapters": normalized,
        },
        "actions": [
            {"id": "confirm", "label": "确认并开始撰写"},
            {"id": "revise", "label": "要求修改", "requires_input": True},
            {"id": "cancel", "label": "取消"},
        ],
        "tool_calls": [],
    }

    logger.info({
        "msg": "request_user_confirmation_interrupt",
        "topic": topic,
        "chapters_count": len(normalized),
        "title": title,
    })

    # resume 值会作为本函数返回值回传给模型
    return interrupt(payload)
