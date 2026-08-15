"""Smoke checks for plan mode helpers (no Mongo required)."""
from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from app.agent.plan_progress import apply_step_status, build_plan_snapshot
from app.agent.plan_mode_middleware import (
    _SIDE_EFFECT_TOOLS,
    is_plan_confirmed_from_messages,
)
from app.agent.tools.local.propose_plan import PROPOSE_PLAN_TOOL
from app.agent.tools.local.update_plan_step import UPDATE_PLAN_STEP_TOOL
from app.schemas.chat import ChatRequest


def test_chat_request_plan_mode_default():
    req = ChatRequest(query="hello")
    assert req.plan_mode is False
    assert req.deep_thinking is False


def test_chat_request_plan_mode_true():
    req = ChatRequest(query="hello", plan_mode=True)
    assert req.plan_mode is True


def test_side_effect_tools():
    assert "begin_report" in _SIDE_EFFECT_TOOLS
    assert "submit_report" in _SIDE_EFFECT_TOOLS
    assert "request_user_confirmation" in _SIDE_EFFECT_TOOLS
    assert PROPOSE_PLAN_TOOL not in _SIDE_EFFECT_TOOLS
    assert UPDATE_PLAN_STEP_TOOL not in _SIDE_EFFECT_TOOLS
    assert "web_search" not in _SIDE_EFFECT_TOOLS


def test_plan_confirmed_from_messages():
    assert is_plan_confirmed_from_messages([]) is False
    revise = ToolMessage(
        content=json.dumps({"action": "revise", "payload": {}}),
        tool_call_id="1",
        name=PROPOSE_PLAN_TOOL,
    )
    assert is_plan_confirmed_from_messages([revise]) is False
    confirm = ToolMessage(
        content=json.dumps({"action": "confirm", "payload": {"steps": []}}),
        tool_call_id="2",
        name=PROPOSE_PLAN_TOOL,
    )
    assert is_plan_confirmed_from_messages([revise, confirm]) is True
    search = ToolMessage(
        content="ok",
        tool_call_id="3",
        name="web_search",
    )
    assert is_plan_confirmed_from_messages([revise, confirm, search]) is True


def test_plan_progress_apply():
    plan = build_plan_snapshot(
        title="测试计划",
        goal="完成任务",
        steps=[
            {"id": "1", "title": "调研", "selected": True},
            {"id": "2", "title": "撰写", "selected": True},
            {"id": "3", "title": "跳过项", "selected": False},
        ],
    )
    assert plan["steps"][2]["status"] == "skipped"
    assert plan["status"] == "pending"
    running = apply_step_status(plan, "1", "running")
    assert running["current_step_id"] == "1"
    assert running["steps"][0]["status"] == "running"
    done = apply_step_status(running, "1", "completed")
    done = apply_step_status(done, "2", "running")
    assert done["steps"][0]["status"] == "completed"
    assert done["steps"][1]["status"] == "running"
    done = apply_step_status(done, "2", "completed")
    assert done["status"] == "completed"
    assert done["completed_count"] == 3


if __name__ == "__main__":
    test_chat_request_plan_mode_default()
    test_chat_request_plan_mode_true()
    test_side_effect_tools()
    test_plan_confirmed_from_messages()
    test_plan_progress_apply()
    print("plan_mode smoke checks passed")
