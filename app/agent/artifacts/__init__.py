"""
    artifacts/__init__.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    产物生命周期：协议注册、父图、提交收口。

"""
from app.agent.artifacts.graph import build_parent_graph, route_after_react
from app.agent.artifacts.handoff import ArtifactHandoffMiddleware
from app.agent.artifacts.protocol import (
    ArtifactProtocol,
    extract_post_begin_content,
    get_protocol,
    get_protocol_by_begin_tool,
    infer_pending_artifact,
    list_begin_tools,
    register_protocol,
    resolve_artifact_context,
)
from app.agent.artifacts.report import REPORT_PROTOCOL
from app.agent.artifacts.finalize import artifact_already_emitted, finalize_artifact

__all__ = [
    "ArtifactHandoffMiddleware",
    "ArtifactProtocol",
    "REPORT_PROTOCOL",
    "artifact_already_emitted",
    "build_parent_graph",
    "extract_post_begin_content",
    "finalize_artifact",
    "get_protocol",
    "get_protocol_by_begin_tool",
    "infer_pending_artifact",
    "list_begin_tools",
    "register_protocol",
    "resolve_artifact_context",
    "route_after_react",
]
