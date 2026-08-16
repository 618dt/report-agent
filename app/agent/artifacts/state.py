"""
    state.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    父图状态：在 Messages 之上附加产物生命周期字段。

"""
from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage


class ArtifactAgentState(TypedDict, total=False):
    """父图状态。messages 与 create_agent 子图对齐。"""

    messages: Annotated[list[AnyMessage], add_messages]
    pending_artifact: Optional[dict[str, Any]]
    artifact_draft: str
    artifact_committed: bool
    search_sources: list[dict[str, str]]
