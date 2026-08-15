"""
    web_search.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    Web search tool powered by Tavily async API.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Literal, Optional

from langchain.tools import tool
from tavily import AsyncTavilyClient

from app.configs import cluster_configs
from app.utils.log import logger

_tavily_client: AsyncTavilyClient | None = None

_SNIPPET_MAX_CHARS = 400
_MAX_RESULTS_MIN = 1
_MAX_RESULTS_MAX = 10
_DEFAULT_TIMEOUT_SECONDS = 30

SearchDepth = Literal["basic", "advanced"]
SearchTopic = Literal["general", "news", "finance"]
TimeRange = Literal["day", "week", "month", "year"]


def _get_tavily_config() -> dict:
    """读取 cluster 中的 tavily 配置

    Returns:
        dict -- tavily 配置字典
    """
    return cluster_configs.get("tavily", {}) or {}


def _get_tavily_client() -> AsyncTavilyClient:
    """Lazy-create a singleton AsyncTavilyClient from cluster configs."""
    global _tavily_client
    if _tavily_client is not None:
        return _tavily_client
    api_key = _get_tavily_config().get("api_key", "")
    if not api_key:
        raise RuntimeError("tavily.api_key is not configured in cluster.configs.yaml")

    _tavily_client = AsyncTavilyClient(api_key=api_key)
    return _tavily_client


def _get_timeout_seconds() -> float:
    """获取 Tavily 搜索超时秒数

    Returns:
        float -- 超时秒数，默认 30
    """
    raw = _get_tavily_config().get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(_DEFAULT_TIMEOUT_SECONDS)
    return value if value > 0 else float(_DEFAULT_TIMEOUT_SECONDS)


def _clamp_max_results(max_results: int) -> int:
    """将 max_results 限制在允许区间

    Arguments:
        max_results -- 调用方传入的结果数

    Returns:
        int -- clamp 后的结果数
    """
    try:
        value = int(max_results)
    except (TypeError, ValueError):
        return 5
    return max(_MAX_RESULTS_MIN, min(_MAX_RESULTS_MAX, value))


def _truncate_snippet(content: str, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    """截断搜索摘要，控制进入模型上下文的体积

    Arguments:
        content -- 原始摘要
        max_chars -- 最大字符数

    Returns:
        str -- 截断后的摘要
    """
    text = (content or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _format_score(score: object) -> str:
    """格式化相关性分数

    Arguments:
        score -- Tavily 返回的 score

    Returns:
        str -- 如 0.85；无效时返回 n/a
    """
    try:
        return f"{float(score):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _format_search_results(results: list[dict]) -> str:
    """将 Tavily 结果格式化为模型可读文本 + SOURCES_JSON

    Arguments:
        results -- Tavily results 列表

    Returns:
        str -- 供模型与前端消费的工具输出
    """
    lines: list[str] = []
    sources: list[dict] = []

    for i, item in enumerate(results, 1):
        title = (item.get("title") or "Untitled").strip()
        url = (item.get("url") or "").strip()
        snippet = _truncate_snippet(item.get("content") or "")
        score = item.get("score")
        score_text = _format_score(score)

        lines.append(f"{i}. {title} (score={score_text})\n   {url}\n   {snippet}")
        if url:
            source: dict = {"title": title, "url": url}
            try:
                source["score"] = round(float(score), 4)
            except (TypeError, ValueError):
                pass
            sources.append(source)

    body = "\n\n".join(lines) if lines else "No results."
    sources_json = json.dumps(sources, ensure_ascii=False, separators=(",", ":"))
    return f"{body}\n\n---\nSOURCES_JSON:{sources_json}"


@tool
async def web_search(
    query: str,
    max_results: int = 5,
    search_depth: SearchDepth = "basic",
    topic: SearchTopic = "general",
    time_range: Optional[TimeRange] = None,
) -> str:
    """Search the web for real-time information via Tavily.

    Use when the user asks about current events, recent data, topic facts,
    or anything that may require up-to-date information beyond training data.

    Args:
        query: Focused search query (include topic name, time, region, metrics).
        max_results: Number of results to return (1-10, default 5).
        search_depth: "basic" for quick lookups; "advanced" for core report evidence.
        topic: "general", "news", or "finance".
        time_range: Optional recency filter: "day", "week", "month", or "year".
    """
    clamped_max = _clamp_max_results(max_results)
    timeout_seconds = _get_timeout_seconds()
    started = time.perf_counter()

    search_kwargs: dict = {
        "query": query,
        "max_results": clamped_max,
        "search_depth": search_depth,
        "topic": topic,
        "timeout": int(timeout_seconds),
    }
    if time_range:
        search_kwargs["time_range"] = time_range

    try:
        client = _get_tavily_client()
        response = await asyncio.wait_for(
            client.search(**search_kwargs),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.error({
            "msg": "tavily_search_timeout",
            "query": query,
            "max_results": clamped_max,
            "search_depth": search_depth,
            "topic": topic,
            "time_range": time_range,
            "timeout_seconds": timeout_seconds,
            "elapsed_ms": elapsed_ms,
        })
        return (
            f"Search timed out after {timeout_seconds:.0f}s for '{query}'. "
            "Try a narrower query or retry later."
        )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.error({
            "msg": "tavily_search_failed",
            "query": query,
            "max_results": clamped_max,
            "search_depth": search_depth,
            "topic": topic,
            "time_range": time_range,
            "elapsed_ms": elapsed_ms,
            "error": str(e),
        }, exc_info=True)
        return f"Search failed: {e}"

    results = response.get("results") or []
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info({
        "msg": "tavily_search_ok",
        "query": query,
        "max_results": clamped_max,
        "search_depth": search_depth,
        "topic": topic,
        "time_range": time_range,
        "result_count": len(results),
        "elapsed_ms": elapsed_ms,
    })

    if not results:
        return f"No results found for '{query}'."

    return _format_search_results(results)
