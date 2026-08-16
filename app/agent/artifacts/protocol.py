"""
    protocol.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    产物生命周期协议注册表：begin_* 之后由父图强制 write → commit。

"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


@dataclass(frozen=True)
class ArtifactProtocol:
    """一种可流式生成、必须由系统提交的产物。"""

    kind: str
    begin_tool: str
    write_prompt: str
    plan_complete_note: str = "已提交"


_REGISTRY: dict[str, ArtifactProtocol] = {}
_BEGIN_TOOLS: dict[str, ArtifactProtocol] = {}


def register_protocol(protocol: ArtifactProtocol) -> ArtifactProtocol:
    """注册产物协议（同 kind / begin_tool 后写覆盖）。"""
    _REGISTRY[protocol.kind] = protocol
    _BEGIN_TOOLS[protocol.begin_tool] = protocol
    return protocol


def get_protocol(kind: str) -> Optional[ArtifactProtocol]:
    """按产物 kind 查找协议。"""
    if not kind:
        return None
    return _REGISTRY.get(str(kind))


def get_protocol_by_begin_tool(tool_name: str) -> Optional[ArtifactProtocol]:
    """按 begin_* 工具名查找协议。"""
    if not tool_name:
        return None
    return _BEGIN_TOOLS.get(str(tool_name))


def list_begin_tools() -> frozenset[str]:
    """已注册的 begin 工具名。"""
    return frozenset(_BEGIN_TOOLS.keys())


def _message_text(message: BaseMessage | None) -> str:
    """提取 AIMessage 纯文本。"""
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(getattr(block, "text", "") or ""))
        return "".join(parts).strip()
    return str(content).strip()


def infer_pending_artifact(messages: list[Any] | None) -> Optional[dict[str, Any]]:
    """从消息中还原最近一次 begin_* 的 pending_artifact。"""
    pending: dict[str, Any] | None = None
    for msg in messages or []:
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", None) or []:
                if not isinstance(tc, dict):
                    continue
                proto = get_protocol_by_begin_tool(str(tc.get("name") or ""))
                if not proto:
                    continue
                args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
                pending = {
                    "kind": proto.kind,
                    "tool_call_id": str(tc.get("id") or ""),
                    "title": str(args.get("title") or ""),
                    "topic": str(args.get("topic") or ""),
                }
        elif isinstance(msg, ToolMessage):
            proto = get_protocol_by_begin_tool(getattr(msg, "name", None) or "")
            if proto and pending is None:
                pending = {
                    "kind": proto.kind,
                    "tool_call_id": str(getattr(msg, "tool_call_id", "") or ""),
                    "title": "",
                    "topic": "",
                }
    return pending


def extract_post_begin_content(messages: list[Any] | None) -> str:
    """begin_* 工具结果之后、无 tool_calls 的助手正文（ReAct 已写稿时的草稿）。"""
    if not messages:
        return ""
    begin_idx = -1
    for i, msg in enumerate(messages):
        name = getattr(msg, "name", None) or ""
        if isinstance(msg, ToolMessage) and get_protocol_by_begin_tool(name):
            begin_idx = i
            continue
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", None) or []:
                if isinstance(tc, dict) and get_protocol_by_begin_tool(
                    str(tc.get("name") or ""),
                ):
                    begin_idx = i
    if begin_idx < 0:
        return ""
    parts: list[str] = []
    for msg in messages[begin_idx + 1:]:
        if not isinstance(msg, AIMessage):
            continue
        if getattr(msg, "tool_calls", None):
            continue
        text = _message_text(msg)
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _tool_call_name(tc: Any) -> str:
    if isinstance(tc, dict):
        return str(tc.get("name") or "")
    return str(getattr(tc, "name", "") or "")


def _append_same_role(out: list[BaseMessage], msg: BaseMessage) -> None:
    """合并连续同角色消息，避免 user/user 或 assistant/assistant 触发部分网关 400。"""
    if not out:
        out.append(msg)
        return
    prev = out[-1]
    if type(prev) is not type(msg):
        out.append(msg)
        return
    if not isinstance(msg, (HumanMessage, AIMessage, SystemMessage)):
        out.append(msg)
        return
    combined = "\n\n".join(
        part for part in (_message_text(prev), _message_text(msg)) if part
    )
    out[-1] = type(msg)(content=combined)


def sanitize_messages_for_write(messages: list[Any] | None) -> list[BaseMessage]:
    """撰写节点不绑定 tools，必须去掉 tool 角色，否则网关会报 tool 无对应 tool_calls。

    Command.PARENT 交接后，父图里经常只有 begin_* 的 ToolMessage、没有带 tool_calls
    的 AIMessage；同时未声明 tools 时部分兼容接口会剥掉历史 tool_calls。
    检索结果转成普通 user 文本，供撰写模型继续使用。
    """
    out: list[BaseMessage] = []
    for msg in messages or []:
        if isinstance(msg, ToolMessage):
            name = str(getattr(msg, "name", "") or "tool")
            text = _message_text(msg)
            if not text:
                continue
            _append_same_role(out, HumanMessage(content=f"[{name} 结果]\n{text}"))
            continue
        if isinstance(msg, AIMessage):
            text = _message_text(msg)
            names = [
                n for n in (
                    _tool_call_name(tc)
                    for tc in (getattr(msg, "tool_calls", None) or [])
                )
                if n
            ]
            if names:
                note = f"（已调用工具：{', '.join(names)}）"
                text = f"{text}\n{note}".strip() if text else note
            if text:
                _append_same_role(out, AIMessage(content=text))
            continue
        if isinstance(msg, (HumanMessage, SystemMessage)):
            if _message_text(msg):
                _append_same_role(out, msg)
    return out


def build_write_messages(
    messages: list[Any] | None,
    write_prompt: str,
) -> list[BaseMessage]:
    """撰写请求消息：去 tool 角色，并合并写作指令，避免连续两条 user。"""
    out = sanitize_messages_for_write(messages)
    prompt = (write_prompt or "").strip()
    if prompt:
        _append_same_role(out, HumanMessage(content=prompt))
    if not out:
        out.append(HumanMessage(content=prompt or "请输出完整正文，不要调用工具。"))
    return out


def resolve_artifact_context(state: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    """合并 state 与消息推断，得到 pending + draft。"""
    state = state or {}
    messages = state.get("messages") or []
    pending = state.get("pending_artifact") or infer_pending_artifact(messages)
    draft = str(state.get("artifact_draft") or "").strip()
    if not draft:
        draft = extract_post_begin_content(messages)
    return pending, draft
