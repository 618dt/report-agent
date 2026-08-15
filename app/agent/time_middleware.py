"""
    time_middleware.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    当前时间中间件：每次调用模型前注入服务器当前时间，避免模型依赖训练截止时间

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)

# 固定东八区，避免 Windows 缺少 tzdata 时 ZoneInfo 不可用
_CST = timezone(timedelta(hours=8))
_TZ_LABEL = "Asia/Shanghai"
_WEEKDAYS_ZH = (
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期日",
)


def _format_now() -> str:
    """格式化当前东八区时间字符串

    Returns:
        str -- 如 2026年07月17日 14:40:00（星期五）Asia/Shanghai (UTC+08:00)
    """
    now = datetime.now(_CST)
    weekday = _WEEKDAYS_ZH[now.weekday()]
    return (
        f"{now.strftime('%Y年%m月%d日 %H:%M:%S')}（{weekday}）"
        f"{_TZ_LABEL} (UTC+08:00)"
    )


class CurrentTimeMiddleware(AgentMiddleware):
    """每次模型调用前注入当前时间上下文

    Agent 单例的 system_prompt 是静态的，不能写死日期。
    本中间件在每次 awrap_model_call 时动态拼接，保证「今天/最新」有准确锚点。
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Async: 将当前时间追加到 system prompt

        Arguments:
            request {ModelRequest} -- 当前的模型请求
            handler {Callable} -- 下一个处理器

        Returns:
            ModelResponse -- 模型响应
        """
        time_addendum = (
            "\n\n## 当前时间\n\n"
            f"现在是：**{_format_now()}**。\n"
            "- 回答涉及「今天 / 本周 / 今年 / 最新 / 近期」时，必须以该时间为准。\n"
            "- 你的训练数据可能过时；涉及时效性事实（新闻、价格、政策、财报等）"
            "必须先使用 web_search / web_fetch 核实，不要用记忆编造。\n"
            "- 叙述年份、季度时不要默认停留在训练截止年。"
        )
        existing_prompt = request.system_prompt or ""
        modified_request = request.override(
            system_prompt=existing_prompt + time_addendum
        )
        return await handler(modified_request)
