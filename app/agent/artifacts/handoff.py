"""
    handoff.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    begin_* 工具成功后把控制权交给父图 write/commit 节点。

"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.agent.artifacts.protocol import get_protocol_by_begin_tool
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
        logger.info({
            "msg": "artifact_handoff_to_parent",
            "kind": protocol.kind,
            "begin_tool": tool_name,
            "tool_call_id": pending["tool_call_id"],
        })
        update: dict[str, Any] = {
            "pending_artifact": pending,
            "artifact_committed": False,
            "artifact_draft": "",
        }
        if isinstance(result, ToolMessage):
            update["messages"] = [result]
        return Command(
            update=update,
            goto="write_artifact",
            graph=Command.PARENT,
        )
