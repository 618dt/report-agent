"""
    graph.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    父图：react 子图 + 强制 write_artifact → commit_artifact。

"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph

from app.agent.artifacts.cite import (
    CITE_SYSTEM_PROMPT,
    enforce_report_citations,
    extract_search_catalog,
    format_catalog_block,
    merge_catalogs,
    sanitize_prompt_examples,
)
from app.agent.artifacts.finalize import finalize_artifact
from app.agent.artifacts.report import REPORT_PROTOCOL  # noqa: F401  注册 report 协议
from app.agent.artifacts.protocol import (
    build_write_messages,
    extract_confirmed_chapters,
    format_chapters_block,
    get_protocol,
    resolve_artifact_context,
    resolve_write_source_messages,
    _message_text,
)
from app.agent.artifacts.state import ArtifactAgentState
from app.agent.thinking_middleware import build_thinking_model_settings
from app.agent.time_middleware import current_time_system_addendum
from app.configs import cluster_configs
from app.utils.log import logger
from app.utils.text_helper import count_chinese_chars

# 条件边目标（测试用）
ROUTE_WRITE = "write_artifact"
ROUTE_COMMIT = "commit_artifact"
ROUTE_END = END


def route_after_react(state: ArtifactAgentState) -> str:
    """react 子图结束后：有未提交产物则 write/commit，否则结束。"""
    if state.get("artifact_committed"):
        return ROUTE_END
    pending, draft = resolve_artifact_context(dict(state))
    if not pending:
        return ROUTE_END
    if draft:
        return ROUTE_COMMIT
    return ROUTE_WRITE


def _runtime_config(config: RunnableConfig | dict[str, Any] | None = None) -> dict[str, Any]:
    """节点内读取 RunnableConfig（参数注入失败时回退 get_config）。"""
    if isinstance(config, dict) and (config.get("configurable") or config.get("run_id")):
        return config
    try:
        resolved = get_config() or {}
    except RuntimeError:
        resolved = {}
    return resolved if isinstance(resolved, dict) else {}


def _config_ids(config: dict[str, Any] | None) -> tuple[str, str]:
    """从 RunnableConfig 读取 conversation_id / run_id。"""
    cfg = _runtime_config(config)
    configurable = cfg.get("configurable") or {}
    conversation_id = str(
        configurable.get("conversation_id")
        or configurable.get("thread_id")
        or "",
    )
    run_id = str(configurable.get("run_id") or "")
    return conversation_id, run_id


def build_parent_graph(*, model, react_agent, checkpointer):
    """编译父图：react → (write) → commit → wrap_up。"""

    async def write_artifact_node(state: ArtifactAgentState) -> dict[str, Any]:
        """无工具撰写节点：只输出产物正文。"""
        pending, _draft = resolve_artifact_context(dict(state))
        pending = pending or {}
        protocol = get_protocol(str(pending.get("kind") or "report"))
        prompt = protocol.write_prompt if protocol else "请输出完整正文，不要调用工具。"
        title = str(pending.get("title") or "")
        topic = str(pending.get("topic") or "")
        raw_messages = resolve_write_source_messages(dict(state))
        catalog = merge_catalogs(
            state.get("search_sources"),
            extract_search_catalog(raw_messages),
        )
        chapters = state.get("confirmed_chapters") or extract_confirmed_chapters(
            raw_messages,
        )
        extra = (
            f"\n\n标题：{title}\n主题：{topic}\n"
            "只输出 Markdown 正文，不要前言或工具调用。"
            f"{format_chapters_block(chapters)}"
            f"{format_catalog_block(catalog)}"
        )
        settings = build_thinking_model_settings(False, disable_thinking=True)
        bound = model.bind(**settings)
        base_system = sanitize_prompt_examples(str(
            (cluster_configs.get("llm") or {}).get("deepseek", {}).get("system_prompt")
            or ""
        ))
        messages = build_write_messages(
            raw_messages,
            prompt + extra,
            system_prompt=(
                f"{base_system}{current_time_system_addendum()}\n\n{CITE_SYSTEM_PROMPT}"
            ).strip(),
        )
        selected_n = sum(
            1 for item in (chapters or [])
            if isinstance(item, dict) and item.get("selected", True)
        )
        logger.info({
            "msg": "artifact_write_start",
            "kind": pending.get("kind"),
            "title": title,
            "source": (
                "subgraph" if state.get("write_source_messages") else "parent"
            ),
            "source_messages": len(raw_messages),
            "write_messages": len(messages),
            "source_catalog": len(catalog),
            "selected_chapters": selected_n,
            "has_web_search": any(
                getattr(msg, "name", "") == "web_search" for msg in raw_messages
            ),
        })
        response = await bound.ainvoke(messages, config=_runtime_config())
        original = _message_text(response)
        text = enforce_report_citations(original, catalog)
        if text != original:
            logger.info({
                "msg": "artifact_citations_rewritten",
                "title": title,
                "original_chars": len(original),
                "rewritten_chars": len(text),
            })
        return {
            "pending_artifact": pending,
            "artifact_draft": text,
            "artifact_committed": False,
            "write_source_messages": [],
        }

    async def commit_artifact_node(state: ArtifactAgentState) -> dict[str, Any]:
        """确定性提交，不经过模型选工具。"""
        pending, draft = resolve_artifact_context(dict(state))
        catalog = merge_catalogs(
            state.get("search_sources"),
            extract_search_catalog(list(state.get("messages") or [])),
        )
        if draft:
            draft = enforce_report_citations(draft, catalog)
        conversation_id, run_id = _config_ids(None)
        kind = str((pending or {}).get("kind") or "report")
        if draft and conversation_id and run_id:
            await finalize_artifact(
                kind=kind,
                markdown=draft,
                meta=pending,
                conversation_id=conversation_id,
                run_id=run_id,
                publish=True,
            )
        else:
            logger.warning({
                "msg": "artifact_commit_skipped_empty",
                "run_id": run_id,
                "has_draft": bool(draft),
            })
        return {
            "pending_artifact": pending,
            "artifact_draft": draft,
            "artifact_committed": True,
        }

    async def wrap_up_node(state: ArtifactAgentState) -> dict[str, Any]:
        """一两句提示查看产物卡片，不再调用 begin_*。"""
        pending, draft = resolve_artifact_context(dict(state))
        pending = pending or {}
        title = str(pending.get("title") or pending.get("topic") or "分析报告")
        zh_count = count_chinese_chars(draft)
        if draft:
            text = (
                f"报告《{title}》已生成（汉字约 {zh_count} 字），"
                "请在报告卡片中查看。"
            )
        else:
            text = "撰写流程已结束，但未得到可用正文。"
        return {
            "messages": [AIMessage(content=text)],
            "pending_artifact": None,
            "artifact_committed": True,
            "artifact_draft": draft,
            "write_source_messages": [],
            "confirmed_chapters": [],
            "search_sources": [],
        }

    builder = StateGraph(ArtifactAgentState)
    builder.add_node("react", react_agent)
    builder.add_node("write_artifact", write_artifact_node)
    builder.add_node("commit_artifact", commit_artifact_node)
    builder.add_node("wrap_up", wrap_up_node)
    builder.add_edge(START, "react")
    builder.add_conditional_edges(
        "react",
        route_after_react,
        {
            ROUTE_WRITE: "write_artifact",
            ROUTE_COMMIT: "commit_artifact",
            ROUTE_END: END,
        },
    )
    builder.add_edge("write_artifact", "commit_artifact")
    builder.add_edge("commit_artifact", "wrap_up")
    builder.add_edge("wrap_up", END)
    return builder.compile(checkpointer=checkpointer)
