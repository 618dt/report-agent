"""
    plan_progress.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    Plan 进度状态：规范化步骤、应用状态更新、构建 SSE payload

"""
from __future__ import annotations

from typing import Any


def normalize_plan_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """规范化计划步骤列表，默认 status=pending；未选中为 skipped

    Arguments:
        steps -- 原始步骤

    Returns:
        list[dict] -- 规范化后的步骤
    """
    normalized: list[dict[str, Any]] = []
    for i, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        selected = bool(step.get("selected", True))
        status = str(step.get("status") or "").strip().lower()
        if status not in {"pending", "running", "completed", "skipped"}:
            status = "pending" if selected else "skipped"
        elif not selected and status == "pending":
            status = "skipped"
        item: dict[str, Any] = {
            "id": str(step.get("id") or str(i + 1)),
            "title": str(step.get("title") or f"步骤 {i + 1}"),
            "description": str(step.get("description") or ""),
            "selected": selected,
            "status": status,
        }
        if step.get("note"):
            item["note"] = str(step.get("note"))
        normalized.append(item)
    return normalized


def build_plan_snapshot(
    *,
    title: str = "",
    goal: str = "",
    steps: list[dict[str, Any]] | None = None,
    risks: list[str] | None = None,
    assumptions: list[str] | None = None,
) -> dict[str, Any]:
    """构建完整计划快照（供落库与 SSE）"""
    norm_steps = normalize_plan_steps(steps)
    completed = sum(
        1 for s in norm_steps if s["status"] in ("completed", "skipped")
    )
    total = len(norm_steps)
    running_id = next(
        (s["id"] for s in norm_steps if s["status"] == "running"),
        None,
    )
    if total and completed >= total:
        status = "completed"
    elif running_id:
        status = "running"
    else:
        status = "pending"
    return {
        "title": title or "执行计划",
        "goal": goal or "",
        "steps": norm_steps,
        "risks": [str(r) for r in (risks or []) if r],
        "assumptions": [str(a) for a in (assumptions or []) if a],
        "status": status,
        "completed_count": completed,
        "total_count": total,
        "current_step_id": running_id,
    }


def apply_step_status(
    plan: dict[str, Any],
    step_id: str,
    status: str,
    note: str = "",
) -> dict[str, Any]:
    """在计划快照上应用单步状态更新，返回新快照

    将某步设为 running 时，其它仍为 running 的步骤自动标为 completed。
    """
    steps = [dict(s) for s in (plan.get("steps") or [])]
    sid = str(step_id)
    st = str(status).strip().lower()
    found = False
    for step in steps:
        if step.get("id") == sid:
            step["status"] = st
            if note:
                step["note"] = note
            found = True
        elif st == "running" and step.get("status") == "running":
            step["status"] = "completed"
    if not found:
        return build_plan_snapshot(
            title=str(plan.get("title") or ""),
            goal=str(plan.get("goal") or ""),
            steps=steps,
            risks=plan.get("risks"),
            assumptions=plan.get("assumptions"),
        )
    return build_plan_snapshot(
        title=str(plan.get("title") or ""),
        goal=str(plan.get("goal") or ""),
        steps=steps,
        risks=plan.get("risks"),
        assumptions=plan.get("assumptions"),
    )
