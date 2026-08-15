"""
    deepseek_chat.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    DeepSeek OpenAI 兼容 Chat 模型：保留 reasoning_content

    langchain-openai 的 ChatOpenAI 会丢弃第三方字段 reasoning_content。
    本子类在流式/非流式路径中将其写入 additional_kwargs，并在出站消息中回传。

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

import json
import time
from typing import Any, Mapping, Optional

import openai
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI


def _extract_reasoning_content(payload: Mapping[str, Any] | None) -> str | None:
    """从 message / delta dict 中提取 reasoning_content

    Arguments:
        payload -- OpenAI 兼容的 message 或 delta 字典

    Returns:
        str | None -- 非空思考文本；无则 None
    """
    if not payload or not isinstance(payload, Mapping):
        return None
    raw = payload.get("reasoning_content")
    if raw is None:
        raw = payload.get("reasoning")
    if raw is None:
        return None
    text = str(raw)
    return text if text else None


class ChatDeepSeekCompat(ChatOpenAI):
    """OpenAI 兼容接口的 DeepSeek Chat，保留 reasoning_content

    入站：写入 ``AIMessage(Chunk).additional_kwargs['reasoning_content']``。
    出站：若 additional_kwargs 含该字段，回写到请求 messages（工具调用多轮需要）。
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        """流式 chunk：在父类转换后注入 reasoning_content 增量"""
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None

        choices = (
            chunk.get("choices")
            or chunk.get("chunk", {}).get("choices")
            or []
        )
        if not choices:
            return generation_chunk

        delta = choices[0].get("delta") or {}
        reasoning = _extract_reasoning_content(delta)
        message = generation_chunk.message
        if reasoning and isinstance(message, AIMessageChunk):
            message.additional_kwargs["reasoning_content"] = reasoning
        return generation_chunk

    def _create_chat_result(
        self,
        response: dict | openai.BaseModel,
        generation_info: dict | None = None,
    ) -> ChatResult:
        """非流式结果：注入完整 reasoning_content"""
        result = super()._create_chat_result(response, generation_info)

        response_dict: dict[str, Any]
        if isinstance(response, dict):
            response_dict = response
        else:
            response_dict = response.model_dump(
                exclude={"choices": {"__all__": {"message": {"parsed"}}}}
            )

        choices = response_dict.get("choices") or []
        if not choices or not result.generations:
            return result

        msg_payload = choices[0].get("message") or {}
        reasoning = _extract_reasoning_content(msg_payload)
        # 部分 SDK 把 reasoning_content 放在 typed object 上而非 dump
        if reasoning is None and not isinstance(response, dict):
            try:
                typed_msg = response.choices[0].message  # type: ignore[attr-defined]
                reasoning = _extract_reasoning_content(
                    getattr(typed_msg, "model_dump", lambda: {})()
                ) or getattr(typed_msg, "reasoning_content", None)
                if reasoning is not None:
                    reasoning = str(reasoning) or None
            except Exception:
                reasoning = None

        message = result.generations[0].message
        if reasoning and isinstance(message, AIMessage):
            message.additional_kwargs["reasoning_content"] = reasoning
        return result

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> dict:
        """出站：把 AIMessage.additional_kwargs.reasoning_content 写回 messages

        DeepSeek 规定：thinking 模式下，带 tool_calls 的历史 assistant 消息
        必须回传 reasoning_content；撰写轮关闭 thinking 后该字段可能为空，
        此时回传空字符串，避免后续再开启 thinking 时 400。
        """
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return payload

        raw_messages: list[BaseMessage] = []
        if isinstance(input_, list):
            raw_messages = [m for m in input_ if isinstance(m, BaseMessage)]
        elif isinstance(input_, BaseMessage):
            raw_messages = [input_]

        raw_ais = [m for m in raw_messages if isinstance(m, AIMessage)]
        payload_ais = [
            m for m in messages
            if isinstance(m, dict) and m.get("role") == "assistant"
        ]

        filled_empty = 0
        filled_value = 0
        for idx, payload_ai in enumerate(payload_ais):
            raw_ai = raw_ais[idx] if idx < len(raw_ais) else None
            reasoning = None
            if raw_ai is not None:
                reasoning = (raw_ai.additional_kwargs or {}).get(
                    "reasoning_content"
                )
            has_tool_calls = bool(payload_ai.get("tool_calls"))
            if raw_ai is not None and getattr(raw_ai, "tool_calls", None):
                has_tool_calls = True

            if reasoning:
                payload_ai["reasoning_content"] = reasoning
                filled_value += 1
            elif has_tool_calls and "reasoning_content" not in payload_ai:
                payload_ai["reasoning_content"] = ""
                filled_empty += 1

        return payload
