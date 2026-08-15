"""
    propose_plan.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    Plan 模式通用计划门禁：通过 LangGraph interrupt 暂停，等待用户确认/修改计划。

"""
from __future__ import annotations

from typing import Any

from langchain.tools import tool
from langgraph.types import interrupt

from app.utils.log import logger

PROPOSE_PLAN_TOOL = "propose_plan"


@tool
def propose_plan(
    title: str,
    goal: str,
    steps: list[dict[str, Any]],
    risks: list[str] | None = None,
    assumptions: list[str] | None = None,
) -> dict[str, Any]:
    """向用户提交可编辑的执行计划（仅 Plan 模式使用）。

    在调用任何副作用工具（如 request_user_confirmation、begin_report、
    submit_report）之前必须先调用本工具并等待用户确认。
    禁止用 Markdown 清单在对话里展示计划代替本工具。

    Arguments:
        title -- 计划面板标题，例如「确认《新能源汽车》调研执行计划」
        goal -- 一句话说明最终要达成的目标
        steps -- 步骤列表，每项含 id/title/description/selected（可选 depends_on）
        risks -- 可选风险提示
        assumptions -- 可选假设前提

    Returns:
        dict -- 用户决策，形如
            {action: confirm|revise|cancel, payload: {steps?, feedback?}}
    """
    normalized: list[dict[str, Any]] = []
    for i, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        item: dict[str, Any] = {
            "id": str(step.get("id") or str(i + 1)),
            "title": str(step.get("title") or f"步骤 {i + 1}"),
            "description": str(step.get("description") or ""),
            "selected": bool(step.get("selected", True)),
        }
        depends_on = step.get("depends_on")
        if depends_on is not None:
            if isinstance(depends_on, list):
                item["depends_on"] = [str(x) for x in depends_on]
            else:
                item["depends_on"] = [str(depends_on)]
        normalized.append(item)

    risk_list = [str(r) for r in (risks or []) if r]
    assumption_list = [str(a) for a in (assumptions or []) if a]

    payload = {
        "reason": "plan_confirm",
        "title": title or "确认执行计划",
        "schema": {
            "type": "plan_confirm",
            "goal": goal or "",
            "steps": normalized,
            "risks": risk_list,
            "assumptions": assumption_list,
        },
        "actions": [
            {"id": "confirm", "label": "确认并开始执行"},
            {"id": "revise", "label": "要求修改", "requires_input": True},
            {"id": "cancel", "label": "取消"},
        ],
        "tool_calls": [],
    }

    logger.info({
        "msg": "propose_plan_interrupt",
        "title": title,
        "steps_count": len(normalized),
        "goal": (goal or "")[:200],
    })

    # resume 值会作为本函数返回值回传给模型
    return interrupt(payload)
