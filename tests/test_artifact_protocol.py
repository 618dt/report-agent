"""产物协议、父图路由、begin 推断（不打模型 / Mongo）。"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

from app.agent.artifacts.graph import ROUTE_COMMIT, ROUTE_WRITE, route_after_react
from app.agent.artifacts.protocol import (
    build_write_messages,
    extract_confirmed_chapters,
    extract_post_begin_content,
    format_chapters_block,
    get_protocol,
    get_protocol_by_begin_tool,
    infer_pending_artifact,
    list_begin_tools,
    resolve_artifact_context,
    resolve_write_source_messages,
    sanitize_messages_for_write,
    sanitize_history_for_model,
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
    assert "系统已进入撰写节点" in built[-1].content


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


def test_format_catalog_block_empty_is_silent():
    from app.agent.artifacts.cite import format_catalog_block

    assert format_catalog_block([]) == ""
    block = format_catalog_block([
        {"title": "年报", "url": "https://picc.example/ar2023"},
    ])
    assert "必须是下表编号" not in block
    assert "从 1 重新编号" in block
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


def test_enforce_report_citations_collapses_adjacent_duplicate_after_url_merge():
    from app.agent.artifacts.cite import enforce_report_citations

    markdown = (
        "估值关注 PE、EV/EBITDA 是否回归历史中枢附近[5][6]。\n\n"
        "## 参考来源\n\n"
        "5. 东鹏估值分析 - https://example.com/val\n"
        "6. 东鹏 PE/EV 中枢 - https://example.com/val\n"
    )
    catalog = [
        {"title": "来源A", "url": "https://a.example/1"},
        {"title": "来源B", "url": "https://b.example/2"},
        {"title": "来源C", "url": "https://c.example/3"},
        {"title": "来源D", "url": "https://d.example/4"},
        {"title": "东鹏估值分析", "url": "https://example.com/val"},
    ]
    out = enforce_report_citations(markdown, catalog)
    body = out.split("## 参考来源")[0]
    assert "[5][5]" not in body
    assert "[6][6]" not in body
    assert "[1][1]" not in body
    assert body.count("[1]") == 1
    assert out.count("https://example.com/val") == 1


def test_enforce_report_citations_collapses_model_written_duplicates():
    from app.agent.artifacts.cite import enforce_report_citations

    markdown = (
        "是否回归历史中枢附近[6][6]。\n\n"
        "## 参考来源\n\n"
        "6. 估值 - https://example.com/val\n"
    )
    catalog = [
        {"title": "a", "url": "https://a.example/1"},
        {"title": "b", "url": "https://b.example/2"},
        {"title": "c", "url": "https://c.example/3"},
        {"title": "d", "url": "https://d.example/4"},
        {"title": "e", "url": "https://e.example/5"},
        {"title": "估值", "url": "https://example.com/val"},
    ]
    out = enforce_report_citations(markdown, catalog)
    body = out.split("## 参考来源")[0]
    assert "[6][6]" not in body
    assert "[1][1]" not in body
    assert body.count("[1]") == 1


def test_extract_confirmed_chapters_prefers_tool_result():
    import json

    messages = [
        AIMessage(
            content="",
            tool_calls=[{
                "id": "c1",
                "name": "request_user_confirmation",
                "args": {
                    "title": "确认大纲",
                    "topic": "东鹏",
                    "chapters": [
                        {"id": "1", "title": "概述", "description": "旧", "selected": True},
                        {"id": "2", "title": "投资建议", "description": "旧", "selected": False},
                    ],
                },
            }],
        ),
        ToolMessage(
            content=json.dumps({
                "action": "confirm",
                "payload": {
                    "chapters": [
                        {"id": "1", "title": "概述", "description": "公司简介", "selected": True},
                        {"id": "2", "title": "投资建议", "description": "短中期", "selected": True},
                        {"id": "3", "title": "风险", "description": "", "selected": False},
                    ],
                },
            }, ensure_ascii=False),
            tool_call_id="c1",
            name="request_user_confirmation",
        ),
    ]
    chapters = extract_confirmed_chapters(messages)
    assert [c["title"] for c in chapters if c["selected"]] == ["概述", "投资建议"]
    block = format_chapters_block(chapters)
    assert "概述" in block
    assert "投资建议" in block
    assert "风险" not in block


def test_resolve_write_source_messages_prefers_subgraph_snapshot():
    parent = [HumanMessage(content="只要父图历史")]
    subgraph = [
        HumanMessage(content="写东鹏报告"),
        ToolMessage(content="保费数据", tool_call_id="s1", name="web_search"),
    ]
    picked = resolve_write_source_messages({
        "messages": parent,
        "write_source_messages": subgraph,
    })
    assert picked == subgraph
    fallback = resolve_write_source_messages({"messages": parent})
    assert fallback == parent


def test_build_write_messages_keeps_search_snippets_and_chapters():
    import json

    built = build_write_messages(
        [
            HumanMessage(content="写东鹏饮料报告，约2000字"),
            ToolMessage(
                content=json.dumps({
                    "action": "confirm",
                    "payload": {"chapters": [
                        {"id": "1", "title": "投资建议", "selected": True},
                    ]},
                }),
                tool_call_id="c1",
                name="request_user_confirmation",
            ),
            ToolMessage(
                content="1. 东鹏年报\n   https://dongpeng.example/ar\n   营收同比增长 30%",
                tool_call_id="c2",
                name="web_search",
            ),
            ToolMessage(content="已开始撰写", tool_call_id="c3", name="begin_report"),
        ],
        "请输出完整报告 Markdown 正文。",
    )
    texts = "\n".join(str(m.content) for m in built)
    assert "营收同比增长 30%" in texts
    assert "web_search" in texts
    assert "请输出完整报告" in texts


def test_sanitize_history_drops_orphan_tool_and_merges_assistants():
    messages = [
        HumanMessage(content="写报告"),
        ToolMessage(content="已开始撰写", tool_call_id="orphan", name="begin_report"),
        AIMessage(content="# 报告正文\n很长"),
        AIMessage(content="报告已生成（汉字约 1170 字），请在报告卡片中查看。"),
        HumanMessage(content="可以的"),
    ]
    cleaned = sanitize_history_for_model(messages)
    assert all(not isinstance(m, ToolMessage) for m in cleaned)
    assert [type(m) for m in cleaned] == [HumanMessage, AIMessage, HumanMessage]
    assert "报告已生成" in cleaned[1].content
    assert cleaned[-1].content == "可以的"


def test_sanitize_history_keeps_paired_tools_and_strips_unmatched():
    messages = [
        HumanMessage(content="搜一下"),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "c1",
                "name": "web_search",
                "args": {"query": "东鹏"},
            }],
        ),
        ToolMessage(content="营收增长", tool_call_id="c1", name="web_search"),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "c2",
                "name": "begin_report",
                "args": {"title": "T", "topic": "X"},
            }],
        ),
        AIMessage(content="报告已生成"),
    ]
    cleaned = sanitize_history_for_model(messages)
    assert isinstance(cleaned[1], AIMessage)
    assert cleaned[1].tool_calls
    assert isinstance(cleaned[2], ToolMessage)
    assert not any(
        getattr(m, "tool_calls", None) and any(
            (tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", ""))
            == "begin_report"
            for tc in (m.tool_calls or [])
        )
        for m in cleaned if isinstance(m, AIMessage)
    )
    assert cleaned[-1].content == "报告已生成"
