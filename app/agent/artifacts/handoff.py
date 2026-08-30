"""
    handoff.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    begin_* 工具成功后把控制权交给父图 write/commit 节点。

"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.agent.artifacts.cite import extract_search_catalog
from app.agent.artifacts.protocol import (
    extract_confirmed_chapters,
    get_protocol_by_begin_tool,
    sanitize_history_for_model,
)
from app.agent.plan_mode_middleware import (
    _tool_call_id_from_request,
    _tool_name_from_request,
)
from app.utils.log import logger


def _tool_args_from_request(request: Any) -> dict[str, Any]:
    """从 ToolCallRequest 提取参数字典。"""
    tool_call = getattr(request, "tool_call", None) or {}
    if isinstance(tool_call, dict):
        args = tool_call.get("args") or tool_call.get("arguments") or {}
        return args if isinstance(args, dict) else {}
    args = getattr(tool_call, "args", None) or getattr(tool_call, "arguments", None)
    return args if isinstance(args, dict) else {}


class ArtifactHandoffMiddleware(AgentMiddleware):
    """begin_* 执行成功后 Command.PARENT 跳转到 write_artifact。"""

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async: begin 工具返回后交接父图。"""
        tool_name = _tool_name_from_request(request)
        protocol = get_protocol_by_begin_tool(tool_name)
        result = await handler(request)
        if protocol is None:
            return result
        if isinstance(result, Command):
            return result
        if isinstance(result, ToolMessage):
            content = str(result.content or "")
            if content.startswith("Plan 模式尚未确认") or content.startswith("错误"):
                return result

        call_id = _tool_call_id_from_request(request)
        args = _tool_args_from_request(request)
        pending = {
            "kind": protocol.kind,
            "tool_call_id": call_id or getattr(result, "tool_call_id", "") or "",
            "title": str(args.get("title") or ""),
            "topic": str(args.get("topic") or ""),
        }
        messages = _messages_from_request(request)
        if isinstance(result, ToolMessage):
            messages = [*messages, result]
        search_sources = extract_search_catalog(messages)
        confirmed_chapters = extract_confirmed_chapters(messages)
        selected_chapters = [
            item for item in confirmed_chapters if item.get("selected", True)
        ]
        logger.info({
            "msg": "artifact_handoff_to_parent",
            "kind": protocol.kind,
            "begin_tool": tool_name,
            "tool_call_id": pending["tool_call_id"],
            "subgraph_messages": len(messages),
            "search_sources": len(search_sources),
            "selected_chapters": len(selected_chapters),
            "tool_results": _tool_result_names(messages),
        })
        update: dict[str, Any] = {
            "pending_artifact": pending,
            "artifact_committed": False,
            "artifact_draft": "",
            "search_sources": search_sources,
            "confirmed_chapters": confirmed_chapters,
        }
        if messages:
            update["write_source_messages"] = messages
        return Command(
            update=update,
            goto="write_artifact",
            graph=Command.PARENT,
        )


def _messages_from_request(request: Any) -> list[Any]:
    """从工具请求中取出当前子图 messages（begin 交接时检索结果还在子图里）。"""
    candidates = [
        getattr(request, "state", None),
        getattr(getattr(request, "runtime", None), "state", None),
    ]
    for state in candidates:
        if state is None:
            continue
        if isinstance(state, dict):
            messages = state.get("messages") or []
            if messages:
                return list(messages)
            continue
        messages = getattr(state, "messages", None) or []
        if messages:
            return list(messages)
    return []


class HistorySanitizeMiddleware(AgentMiddleware):
    """后续轮次调用模型前，清掉交接留下的孤立 tool / 连续 assistant。"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse] | ModelResponse],
    ) -> ModelResponse:
        """Async: 清洗 messages 后再交给下游。"""
        original = list(request.messages or [])
        cleaned = sanitize_history_for_model(original)
        if cleaned == original:
            return await handler(request)
        logger.info({
            "msg": "history_sanitized_for_model",
            "before": len(original),
            "after": len(cleaned),
        })
        return await handler(request.override(messages=cleaned))


def _tool_result_names(messages: list[Any]) -> list[str]:
    """交接日志：子图里出现过的工具结果名。"""
    names: list[str] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            name = str(getattr(msg, "name", "") or "tool")
            if name not in names:
                names.append(name)
    return names
