"""产物协议、父图路由、begin 推断（不打模型 / Mongo）。"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

from app.agent.artifacts.graph import ROUTE_COMMIT, ROUTE_WRITE, route_after_react
from app.agent.artifacts.protocol import (
    build_write_messages,
    extract_post_begin_content,
    get_protocol,
    get_protocol_by_begin_tool,
    infer_pending_artifact,
    list_begin_tools,
    resolve_artifact_context,
    sanitize_messages_for_write,
)
from app.agent.artifacts.report import REPORT_PROTOCOL
from app.agent.tools.local.begin_report import BEGIN_REPORT_TOOL


def test_report_protocol_registered():
    assert REPORT_PROTOCOL.kind == "report"
    assert get_protocol("report") is REPORT_PROTOCOL
    assert get_protocol_by_begin_tool(BEGIN_REPORT_TOOL) is REPORT_PROTOCOL
    assert BEGIN_REPORT_TOOL in list_begin_tools()


def test_infer_pending_from_begin_tool_call():
    messages = [
        HumanMessage(content="写报告"),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_1",
                "name": "begin_report",
                "args": {"title": "T", "topic": "X"},
            }],
        ),
        ToolMessage(
            content="已开始撰写",
            tool_call_id="call_1",
            name="begin_report",
        ),
    ]
    pending = infer_pending_artifact(messages)
    assert pending is not None
    assert pending["kind"] == "report"
    assert pending["title"] == "T"
    assert pending["topic"] == "X"
    assert pending["tool_call_id"] == "call_1"


def test_extract_post_begin_content():
    messages = [
        ToolMessage(content="ok", tool_call_id="c", name="begin_report"),
        AIMessage(content="# 报告正文\n\nhello"),
    ]
    assert "报告正文" in extract_post_begin_content(messages)
    assert extract_post_begin_content([]) == ""


def test_route_after_react_no_pending():
    assert route_after_react({"messages": [HumanMessage(content="hi")]}) == END


def test_route_after_react_write_when_begin_no_draft():
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "c1",
                    "name": "begin_report",
                    "args": {"title": "T", "topic": "X"},
                }],
            ),
            ToolMessage(content="ok", tool_call_id="c1", name="begin_report"),
        ],
        "artifact_committed": False,
    }
    assert route_after_react(state) == ROUTE_WRITE


def test_route_after_react_commit_when_draft_exists():
    state = {
        "messages": [
            ToolMessage(content="ok", tool_call_id="c1", name="begin_report"),
            AIMessage(content="# 已写完的报告"),
        ],
        "artifact_committed": False,
    }
    assert route_after_react(state) == ROUTE_COMMIT


def test_route_after_react_end_when_committed():
    state = {
        "pending_artifact": {"kind": "report"},
        "artifact_draft": "# x",
        "artifact_committed": True,
        "messages": [],
    }
    assert route_after_react(state) == END


def test_sanitize_messages_for_write_drops_orphan_tool_role():
    messages = [
        HumanMessage(content="写报告"),
        AIMessage(content="先检索", tool_calls=[{
            "id": "c1",
            "name": "web_search",
            "args": {"query": "PICC"},
        }]),
        ToolMessage(content="保费 7383 亿", tool_call_id="c1", name="web_search"),
        ToolMessage(content="已开始撰写", tool_call_id="c2", name="begin_report"),
    ]
    sanitized = sanitize_messages_for_write(messages)
    assert all(not isinstance(m, ToolMessage) for m in sanitized)
    assert all(not getattr(m, "tool_calls", None) for m in sanitized)
    texts = "\n".join(str(m.content) for m in sanitized)
    assert "保费 7383 亿" in texts
    assert "web_search" in texts
    assert "begin_report" in texts


def test_sanitize_messages_for_write_merges_consecutive_humans():
    sanitized = sanitize_messages_for_write([
        HumanMessage(content="目录已确认"),
        ToolMessage(content="搜索结果 A", tool_call_id="c1", name="web_search"),
        ToolMessage(content="搜索结果 B", tool_call_id="c2", name="web_search"),
    ])
    assert len(sanitized) == 1
    assert isinstance(sanitized[0], HumanMessage)
    assert "目录已确认" in sanitized[0].content
    assert "搜索结果 A" in sanitized[0].content
    assert "搜索结果 B" in sanitized[0].content


def test_build_write_messages_merges_prompt_and_has_no_tool_role():
    built = build_write_messages(
        [
            HumanMessage(content="写中国人保报告"),
            ToolMessage(content="已开始撰写", tool_call_id="c2", name="begin_report"),
        ],
        "请输出完整报告 Markdown 正文。",
    )
    assert all(not isinstance(m, ToolMessage) for m in built)
    assert all(not getattr(m, "tool_calls", None) for m in built)
    assert isinstance(built[-1], HumanMessage)
    assert "请输出完整报告" in built[-1].content
    assert "已开始撰写" in built[-1].content


def test_resolve_artifact_context_prefers_state_draft():
    pending, draft = resolve_artifact_context({
        "pending_artifact": {"kind": "report", "title": "A"},
        "artifact_draft": "from-state",
        "messages": [
            ToolMessage(content="ok", tool_call_id="c", name="begin_report"),
            AIMessage(content="from-messages"),
        ],
    })
    assert pending["title"] == "A"
    assert draft == "from-state"


def test_extract_search_catalog_from_sources_json():
    from app.agent.artifacts.cite import extract_search_catalog

    payload = (
        '1. 人保年报\n   https://picc.example/ar2023\n\n'
        '---\nSOURCES_JSON:'
        '[{"title":"人保年报","url":"https://picc.example/ar2023"},'
        '{"title":"估值","url":"https://wind.example/picc"}]'
    )
    catalog = extract_search_catalog([
        ToolMessage(content=payload, tool_call_id="c1", name="web_search"),
        ToolMessage(content=payload, tool_call_id="c2", name="web_search"),
    ])
    assert len(catalog) == 2
    assert catalog[0]["url"] == "https://picc.example/ar2023"
    assert catalog[1]["url"] == "https://wind.example/picc"


def test_enforce_report_citations_attaches_url_to_bibliography():
    from app.agent.artifacts.cite import enforce_report_citations

    markdown = (
        "净利润同比增长。[1]\n\n"
        "## 参考来源\n\n"
        "[1] 中国人民保险集团2023年度报告，第12—18页，股东及业务结构部分。\n"
    )
    catalog = [{
        "title": "中国人民保险集团股份有限公司2023年年度报告",
        "url": "https://picc.example/ar2023",
    }]
    out = enforce_report_citations(markdown, catalog)
    assert "https://picc.example/ar2023" in out
    assert "1. " in out
    assert "[1]" in out


def test_enforce_report_citations_keeps_unmapped_inline_cite():
    from app.agent.artifacts.cite import enforce_report_citations

    markdown = "结论成立 [9]\n\n## 参考来源\n\n1. 年报 - https://picc.example/ar2023\n"
    catalog = [{"title": "年报", "url": "https://picc.example/ar2023"}]
    out = enforce_report_citations(markdown, catalog)
    assert "[9]" in out or "[1]" in out


def test_enforce_report_citations_keeps_existing_url():
    from app.agent.artifacts.cite import enforce_report_citations

    markdown = (
        "数据见年报。[1]\n\n"
        "## 参考来源\n\n"
        "1. 人保年报 - https://picc.example/ar2023\n"
    )
    catalog = [{
        "title": "人保年报",
        "url": "https://picc.example/ar2023",
    }]
    out = enforce_report_citations(markdown, catalog)
    assert out.count("https://picc.example/ar2023") == 1
    assert "## 参考来源" in out
