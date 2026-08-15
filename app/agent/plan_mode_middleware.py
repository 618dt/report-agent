"""
    plan_mode_middleware.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    Plan 模式中间件：注入规划约束，并在未确认前硬拦截副作用工具

"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.types import Command

from app.agent.tools.local.begin_report import BEGIN_REPORT_TOOL
from app.agent.tools.local.propose_plan import PROPOSE_PLAN_TOOL
from app.agent.tools.local.submit_report import SUBMIT_REPORT_TOOL
from app.utils.log import logger

# 规划未确认前禁止调用的副作用工具
_SIDE_EFFECT_TOOLS: frozenset[str] = frozenset({
    BEGIN_REPORT_TOOL,
    SUBMIT_REPORT_TOOL,
    "request_user_confirmation",
})

_PLAN_MODE_PROMPT = """

## Plan 模式（已开启）

你当前处于 Plan 模式。必须先规划、经用户确认后再执行。

### 强制规则
1. 在调用任何副作用工具之前，必须先调用 `propose_plan` 提交可编辑计划并等待确认。
2. 副作用工具包括：`request_user_confirmation`、`begin_report`、`submit_report`。
3. 规划阶段允许：向用户澄清、`web_search` / `web_fetch`（只读调研）、`load_skill`、`propose_plan`。
4. **禁止**用 Markdown 清单/表格在对话正文里展示计划；计划只能通过 `propose_plan` 的 steps 传递。
5. 用户 `confirm` 后：严格按返回的最终 `selected` 步骤执行（含任务技能要求的二次确认，如报告目录）。
6. 用户 `revise`：根据 feedback 调整后**再次**调用 `propose_plan`，不得直接执行。
7. 用户 `cancel`：礼貌结束，不调用副作用工具。

### 执行进度（确认后强制）
确认计划后，必须用 `update_plan_step` 同步进度，前端据此展示可视化进度条：
1. **开始**某一步前：`update_plan_step(step_id, status=\"running\")`
2. **完成**该步后：`update_plan_step(step_id, status=\"completed\")`（可带简短 note）
3. 跳过未执行步骤：`status=\"skipped\"`
4. `step_id` 必须与确认后的 steps[].id 一致；按顺序推进，同一时刻通常只有一步为 running。

### 计划步骤建议
- 步骤应覆盖「做什么、按什么顺序、依赖关系」，不要把报告章节细目塞进 steps。
- 若任务是报告：steps 应包含「确认章节目录 → 检索资料 → 撰写并提交报告」等阶段；章节细节仍走 `request_user_confirmation`。
"""


def _resolve_plan_flags() -> tuple[bool, bool]:
    """从 configurable 读取 plan_mode / plan_confirmed

    Returns:
        tuple[bool, bool] -- (plan_mode, plan_confirmed)
    """
    try:
        config = get_config() or {}
    except RuntimeError:
        return False, False
    configurable = config.get("configurable") or {}
    return (
        bool(configurable.get("plan_mode", False)),
        bool(configurable.get("plan_confirmed", False)),
    )


def _parse_tool_content(content: Any) -> Any:
    """解析 ToolMessage.content 为 Python 对象"""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return content
    text = content.strip()
    if not text:
        return content
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        # 兼容 Python dict 字符串
        if text.startswith("{") and text.endswith("}"):
            try:
                import ast
                return ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return content
        return content


def is_plan_confirmed_from_messages(messages: Sequence[Any] | None) -> bool:
    """从消息历史判断本轮是否已确认计划

    取最近一次 propose_plan 的工具返回；action=confirm 视为已确认。

    Arguments:
        messages -- 当前请求消息列表

    Returns:
        bool -- 是否已确认
    """
    if not messages:
        return False
    for msg in reversed(list(messages)):
        if not isinstance(msg, ToolMessage):
            continue
        name = getattr(msg, "name", None) or ""
        if name != PROPOSE_PLAN_TOOL:
            continue
        parsed = _parse_tool_content(getattr(msg, "content", None))
        if isinstance(parsed, dict):
            return parsed.get("action") == "confirm"
        return False
    return False


def _tool_name_from_request(request: Any) -> str:
    """从 ToolCallRequest 提取工具名"""
    tool_call = getattr(request, "tool_call", None) or {}
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    name = getattr(tool_call, "name", None)
    if name:
        return str(name)
    tool = getattr(request, "tool", None)
    return str(getattr(tool, "name", "") or "")


def _tool_call_id_from_request(request: Any) -> str:
    """从 ToolCallRequest 提取 tool_call_id"""
    tool_call = getattr(request, "tool_call", None) or {}
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "")
    return str(getattr(tool_call, "id", "") or "")


class PlanModeMiddleware(AgentMiddleware):
    """Plan 模式：提示词门禁 + 副作用工具硬拦截

    - plan_mode=false：不注入、不拦截
    - plan_mode=true 且未确认：拦截副作用工具，返回错误 ToolMessage
    - 确认状态优先看 configurable.plan_confirmed，其次看消息中 propose_plan 结果
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Async: Plan 开启时向 system prompt 追加规划约束"""
        plan_mode, _ = _resolve_plan_flags()
        if not plan_mode:
            return await handler(request)

        existing_prompt = request.system_prompt or ""
        modified = request.override(
            system_prompt=existing_prompt + _PLAN_MODE_PROMPT,
        )
        logger.info({"msg": "plan_mode_prompt_injected"})
        return await handler(modified)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async: 未确认计划前拦截副作用工具"""
        plan_mode, plan_confirmed_flag = _resolve_plan_flags()
        tool_name = _tool_name_from_request(request)

        if not plan_mode or tool_name not in _SIDE_EFFECT_TOOLS:
            return await handler(request)

        state = getattr(request, "state", None) or {}
        messages = state.get("messages") if isinstance(state, dict) else None
        confirmed = plan_confirmed_flag or is_plan_confirmed_from_messages(
            messages,
        )

        if confirmed:
            return await handler(request)

        call_id = _tool_call_id_from_request(request)
        logger.warning({
            "msg": "plan_mode_side_effect_blocked",
            "tool": tool_name,
            "tool_call_id": call_id,
        })
        return ToolMessage(
            content=(
                f"Plan 模式尚未确认执行计划，禁止调用 `{tool_name}`。"
                f"请先调用 `{PROPOSE_PLAN_TOOL}` 提交计划并等待用户 confirm。"
            ),
            tool_call_id=call_id or tool_name,
            name=tool_name,
        )
