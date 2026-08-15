"""
    update_plan_step.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    Plan 模式：更新执行计划中某一步的状态（running / completed / skipped）

"""
from __future__ import annotations

from langchain.tools import tool

from app.utils.log import logger

UPDATE_PLAN_STEP_TOOL = "update_plan_step"

_ALLOWED_STATUSES = frozenset({"pending", "running", "completed", "skipped"})


@tool
def update_plan_step(
    step_id: str,
    status: str,
    note: str = "",
) -> str:
    """更新当前 Plan 中某一步的执行状态（仅 Plan 模式、用户确认计划后使用）。

    执行流程中必须调用本工具同步进度，前端会据此展示步骤列表进度。
    - 开始执行某一步时：status=\"running\"
    - 完成某一步时：status=\"completed\"
    - 跳过某一步时：status=\"skipped\"

    Arguments:
        step_id -- 计划步骤 id（与 propose_plan / 用户确认后的 steps[].id 一致）
        status -- pending | running | completed | skipped
        note -- 可选备注（如完成摘要）

    Returns:
        str -- 更新结果说明
    """
    sid = str(step_id or "").strip()
    st = str(status or "").strip().lower()
    if not sid:
        return "错误：step_id 不能为空"
    if st not in _ALLOWED_STATUSES:
        return (
            f"错误：status 必须是 {', '.join(sorted(_ALLOWED_STATUSES))} 之一，"
            f"收到: {status!r}"
        )

    logger.info({
        "msg": "update_plan_step",
        "step_id": sid,
        "status": st,
        "note": (note or "")[:200],
    })
    note_part = f"；备注：{note.strip()}" if (note or "").strip() else ""
    return f"已将步骤 {sid} 更新为 {st}{note_part}"
