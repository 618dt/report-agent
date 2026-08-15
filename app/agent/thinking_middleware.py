"""
    thinking_middleware.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    深度思考中间件：按请求开关 thinking（关=disabled，开=max）

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.config import get_config

from app.agent.tools.local.begin_report import BEGIN_REPORT_TOOL
from app.agent.tools.local.submit_report import SUBMIT_REPORT_TOOL
from app.utils.log import logger

# DeepSeek：深度思考开 → enabled + max；关 → disabled（闲聊/简单对话不思考）
_THINKING_ENABLED = {"thinking": {"type": "enabled"}}
_THINKING_DISABLED = {"thinking": {"type": "disabled"}}


def _resolve_deep_thinking() -> bool:
    """从当前 RunnableConfig.configurable 读取 deep_thinking

    Returns:
        bool -- True 表示开启深度思考（max）
    """
    try:
        config = get_config() or {}
    except RuntimeError:
        return False
    configurable = config.get("configurable") or {}
    return bool(configurable.get("deep_thinking", False))


def _should_disable_thinking_for_report(messages: Sequence[Any] | None) -> bool:
    """报告撰写及相关收尾轮是否应关闭 thinking

    自 begin_report 起关闭，直到 submit 之后又出现新的 HumanMessage。
    若 submit 后立刻重新开启 thinking，上一轮（thinking=off）带 tool_calls 的
    assistant 消息没有 reasoning_content，DeepSeek 会 400：
    "reasoning_content in the thinking mode must be passed back"。

    Arguments:
        messages -- 当前模型请求中的消息列表

    Returns:
        bool -- True 表示应 disable thinking
    """
    if not messages:
        return False

    begin_idx: int | None = None
    submit_idx: int | None = None
    last_human_idx: int | None = None

    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            last_human_idx = i
            continue
        if not isinstance(msg, ToolMessage):
            continue
        name = getattr(msg, "name", None) or ""
        if name == BEGIN_REPORT_TOOL:
            begin_idx = i
        elif name == SUBMIT_REPORT_TOOL:
            submit_idx = i

    if begin_idx is None:
        return False
    # 尚未 submit：撰写正文轮
    if submit_idx is None or submit_idx < begin_idx:
        return True
    # 已 submit：收尾轮仍关闭，直到用户下一条消息
    if last_human_idx is not None and last_human_idx > submit_idx:
        return False
    return True


# 兼容旧名
_is_report_writing_turn = _should_disable_thinking_for_report


def build_thinking_model_settings(
    deep_thinking: bool,
    *,
    disable_thinking: bool = False,
) -> dict[str, Any]:
    """构建注入到模型调用的 thinking 相关设置

    Arguments:
        deep_thinking -- 是否开启深度思考（True=enabled+max，False=disabled）
        disable_thinking -- 为 True 时强制关闭（报告撰写/收尾轮）

    Returns:
        dict -- model_settings（reasoning_effort + extra_body）
    """
    if disable_thinking or not deep_thinking:
        return {
            "extra_body": dict(_THINKING_DISABLED),
        }

    return {
        "reasoning_effort": "max",
        "extra_body": dict(_THINKING_ENABLED),
    }


class ThinkingMiddleware(AgentMiddleware):
    """每次模型调用前注入 thinking 开关

    Agent 为单例，是否深度思考由 configurable.deep_thinking 传入：
    - False：关闭 thinking（默认，适合闲聊/简单对话）
    - True：开启 thinking，reasoning_effort=max

    报告撰写至 submit 后收尾完成前强制关闭 thinking，避免正文进思考块，
    并防止 disable→enable 切换触发 DeepSeek reasoning_content 回传校验失败。
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Async: 覆盖 model_settings 注入思考参数

        Arguments:
            request {ModelRequest} -- 当前的模型请求
            handler {Callable} -- 下一个处理器

        Returns:
            ModelResponse -- 模型响应
        """
        deep_thinking = _resolve_deep_thinking()
        report_phase = _should_disable_thinking_for_report(request.messages)
        thinking_off = report_phase or not deep_thinking
        thinking_settings = build_thinking_model_settings(
            deep_thinking,
            disable_thinking=report_phase,
        )
        merged = {
            **(request.model_settings or {}),
            **thinking_settings,
        }
        # extra_body 需浅合并，避免覆盖调用方已有字段
        prev_extra = (request.model_settings or {}).get("extra_body") or {}
        if isinstance(prev_extra, dict):
            merged["extra_body"] = {
                **prev_extra,
                **thinking_settings["extra_body"],
            }
        # 关闭思考时去掉可能残留的 reasoning_effort
        if thinking_off:
            merged.pop("reasoning_effort", None)

        logger.info({
            "msg": "thinking_middleware_applied",
            "deep_thinking": deep_thinking,
            "report_writing_turn": report_phase,
            "thinking_enabled": not thinking_off,
            "reasoning_effort": merged.get("reasoning_effort"),
        })
        return await handler(request.override(model_settings=merged))
