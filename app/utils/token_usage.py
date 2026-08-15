"""
    token_usage.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    LLM token 用量估算与 Run 级累加。

    流式过程用字符启发式估算 output；收到模型 API 的 usage 后校正为权威值。

"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

# 基本汉字（估算时按约 1 token/字）
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")

# 估算增量至少变化多少才推送 SSE，避免每个字符都刷事件
_EMIT_MIN_TOKEN_DELTA = 4


def estimate_tokens(text: str) -> int:
    """启发式估算文本 token 数（非计费依据）。

    CJK 约 1 token/字，其余约 4 字符/token。用于流式过程中的实时展示。

    Arguments:
        text -- 任意字符串

    Returns:
        int -- 估算 token 数；空串为 0
    """
    if not text or not isinstance(text, str):
        return 0
    cjk = len(_CJK_CHAR_RE.findall(text))
    other = max(0, len(text) - cjk)
    return cjk + (other + 3) // 4


def _as_int(value: Any) -> int:
    """安全转为非负 int。"""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def normalize_usage_metadata(usage: Mapping[str, Any] | None) -> dict[str, int] | None:
    """将 LangChain usage_metadata / OpenAI token_usage 归一化为统一结构。

    Arguments:
        usage -- usage_metadata 或 response_metadata.token_usage

    Returns:
        dict | None -- {input_tokens, output_tokens, total_tokens}；无效则 None
    """
    if not usage or not isinstance(usage, Mapping):
        return None

    input_tokens = usage.get("input_tokens")
    if input_tokens is None:
        input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("output_tokens")
    if output_tokens is None:
        output_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")

    inp = _as_int(input_tokens)
    out = _as_int(output_tokens)
    total = _as_int(total_tokens)
    if total <= 0:
        total = inp + out
    if inp <= 0 and out <= 0 and total <= 0:
        return None
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": total,
    }


def extract_usage_from_message(message: Any) -> dict[str, int] | None:
    """从 AIMessage / AIMessageChunk 提取归一化 usage。"""
    if message is None:
        return None
    direct = normalize_usage_metadata(getattr(message, "usage_metadata", None))
    if direct:
        return direct
    meta = getattr(message, "response_metadata", None) or {}
    if isinstance(meta, Mapping):
        return normalize_usage_metadata(meta.get("token_usage") or meta.get("usage"))
    return None


@dataclass
class RunUsageTracker:
    """单次 ChatRun 的 token 累加器（已提交 + 当前轮估算）。"""

    committed_input: int = 0
    committed_output: int = 0
    turn_output_text: str = ""
    turn_reasoning_text: str = ""
    model: str = ""
    _last_emitted_total: int = field(default=-1, repr=False)
    _turn_committed: bool = field(default=False, repr=False)

    @classmethod
    def from_committed(
        cls,
        usage: Optional[Mapping[str, Any]] = None,
        *,
        model: str = "",
    ) -> "RunUsageTracker":
        """从已落库的 usage 恢复累加基线（HITL resume 场景）。"""
        tracker = cls(model=model or "")
        if not usage or not isinstance(usage, Mapping):
            return tracker
        tracker.committed_input = _as_int(usage.get("input_tokens"))
        tracker.committed_output = _as_int(usage.get("output_tokens"))
        if usage.get("model"):
            tracker.model = str(usage.get("model"))
        tracker._last_emitted_total = tracker.total_tokens
        return tracker

    @property
    def estimated_turn_output(self) -> int:
        """当前轮尚未提交的 output 估算（正文 + 思考）。"""
        if self._turn_committed:
            return 0
        return estimate_tokens(self.turn_output_text) + estimate_tokens(
            self.turn_reasoning_text
        )

    @property
    def input_tokens(self) -> int:
        return self.committed_input

    @property
    def output_tokens(self) -> int:
        return self.committed_output + self.estimated_turn_output

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated(self) -> bool:
        """是否仍含未校正的估算部分。"""
        return self.estimated_turn_output > 0

    def begin_turn(self) -> None:
        """进入下一轮模型生成：收尾上一轮估算并重置缓冲。"""
        # 上一轮若未收到 API usage，将估算并入 committed，避免轮次切换时清零
        if not self._turn_committed:
            est = self.estimated_turn_output
            if est > 0:
                self.committed_output += est
        self.turn_output_text = ""
        self.turn_reasoning_text = ""
        self._turn_committed = False

    def add_output_delta(self, text: str) -> bool:
        """追加正文/工具参数等输出文本，返回是否建议推送 SSE。"""
        if not text or self._turn_committed:
            return False
        self.turn_output_text += text
        return self._should_emit()

    def add_reasoning_delta(self, text: str) -> bool:
        """追加思考文本，返回是否建议推送 SSE。"""
        if not text or self._turn_committed:
            return False
        self.turn_reasoning_text += text
        return self._should_emit()

    def commit_usage(
        self,
        usage: Mapping[str, Any] | None,
        *,
        model: str = "",
    ) -> bool:
        """用 API 权威 usage 提交当前轮并累加。

        Arguments:
            usage -- 归一化前/后的 usage 字典
            model -- 可选模型名

        Returns:
            bool -- 是否成功提交（无效或本轮已提交则 False）
        """
        if self._turn_committed:
            return False
        normalized = normalize_usage_metadata(usage)
        if not normalized:
            return False
        if model:
            self.model = model
        # 用权威值替换本轮估算：先清估算再累加 API 计数
        self.turn_output_text = ""
        self.turn_reasoning_text = ""
        self.committed_input += normalized["input_tokens"]
        self.committed_output += normalized["output_tokens"]
        self._turn_committed = True
        return True

    def snapshot(self, *, mark_emitted: bool = True) -> dict[str, Any]:
        """生成 SSE / 落库用的用量快照。"""
        data = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated": self.estimated,
        }
        if self.model:
            data["model"] = self.model
        if mark_emitted:
            self._last_emitted_total = self.total_tokens
        return data

    def final_snapshot(self) -> dict[str, Any]:
        """终态快照：不再标记为估算（用于落库 / done）。"""
        data = self.snapshot(mark_emitted=True)
        # 若仍有未校正估算，保留 estimated=True，避免伪造成权威值
        return data

    def _should_emit(self) -> bool:
        total = self.total_tokens
        if self._last_emitted_total < 0:
            return total > 0
        return (total - self._last_emitted_total) >= _EMIT_MIN_TOKEN_DELTA
