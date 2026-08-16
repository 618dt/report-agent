"""
    run_event_bus.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    ChatRun 事件总线：基于 Redis Stream 扇出 SSE 事件

    Key: chat:run:{run_id}:stream
    每条消息字段: payload (JSON 字符串)

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

from app.utils.log import logger
from app.utils.redis import get_async_client

# Stream 近似裁剪上限
_STREAM_MAXLEN = 10000
# 终态后 TTL（秒）
_STREAM_TTL_SECONDS = 3600
# XREAD block 毫秒
_XREAD_BLOCK_MS = 2000

_TERMINAL_TYPES = frozenset({
    "done",
    "interrupted",
    "error",
    "cancelled",
})


def stream_key(run_id: str) -> str:
    """Redis Stream key。"""
    return f"chat:run:{run_id}:stream"


def is_terminal_event(payload: dict[str, Any]) -> bool:
    """是否为结束订阅的终态事件。"""
    return str(payload.get("type") or "") in _TERMINAL_TYPES


def event_seq(payload: dict[str, Any]) -> int:
    """解析事件 seq；无法解析时视为 -1（不占业务序号）。"""
    seq = payload.get("seq") if isinstance(payload, dict) else None
    try:
        return int(seq) if seq is not None else -1
    except (TypeError, ValueError):
        return -1


def should_persist_to_stream(payload: dict[str, Any]) -> bool:
    """seq < 0 的增量（思考 delta、usage 快照）不写 Stream：回放会跳过它们。"""
    return event_seq(payload) >= 0


async def publish_run_event(run_id: str, payload: dict[str, Any]) -> Optional[str]:
    """将一条 SSE JSON 载荷写入 Redis Stream。

    seq < 0 的事件直接跳过（不占连接）。

    Returns:
        str | None -- Stream entry id，失败或跳过时 None
    """
    if not run_id or not isinstance(payload, dict):
        return None
    if not should_persist_to_stream(payload):
        return None
    key = stream_key(run_id)
    body = json.dumps(payload, ensure_ascii=False)
    try:
        client = get_async_client()
        entry_id = await client.xadd(
            key,
            {"payload": body},
            maxlen=_STREAM_MAXLEN,
            approximate=True,
        )
        if is_terminal_event(payload):
            await client.expire(key, _STREAM_TTL_SECONDS)
        return entry_id
    except Exception as e:
        logger.exception({
            "msg": "run_event_publish_failed",
            "run_id": run_id,
            "type": payload.get("type"),
            "seq": payload.get("seq"),
            "error": str(e),
        })
        return None


async def replay_run_events(
    run_id: str,
    after_seq: int = 0,
) -> list[tuple[str, dict[str, Any]]]:
    """从 Redis Stream 回放 seq > after_seq 的事件（按 Stream 顺序）。

    Returns:
        list[(entry_id, payload)]
    """
    key = stream_key(run_id)
    results: list[tuple[str, dict[str, Any]]] = []
    try:
        client = get_async_client()
        # 0-0 起读全量，再按 seq 过滤（事件量受 MAXLEN 约束）
        entries = await client.xrange(key, min="-", max="+")
    except Exception:
        logger.exception({
            "msg": "run_event_replay_failed",
            "run_id": run_id,
        })
        return results

    for entry_id, fields in entries or []:
        payload = _parse_fields(fields)
        if payload is None:
            continue
        seq_int = event_seq(payload)
        # seq < 0 不回放（思考 delta / usage 只在 live 阶段经内存 hub 下发）
        if seq_int < 0 or seq_int <= after_seq:
            continue
        results.append((str(entry_id), payload))
    return results


async def iter_live_run_events(
    run_id: str,
    last_id: str = "$",
    *,
    should_stop=None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """阻塞式 XREAD 实时事件。

    Arguments:
        run_id -- run ID
        last_id -- 上次读到的 Stream id；`$` 表示仅新消息
        should_stop -- 可选回调，返回 True 时结束迭代
    """
    key = stream_key(run_id)
    cursor = last_id or "$"
    client = get_async_client()
    while True:
        if should_stop is not None and should_stop():
            return
        try:
            resp = await client.xread(
                {key: cursor},
                block=_XREAD_BLOCK_MS,
                count=100,
            )
        except Exception:
            logger.exception({
                "msg": "run_event_xread_failed",
                "run_id": run_id,
            })
            return
        if not resp:
            if should_stop is not None and should_stop():
                return
            continue
        for _name, entries in resp:
            for entry_id, fields in entries:
                cursor = str(entry_id)
                payload = _parse_fields(fields)
                if payload is None:
                    continue
                yield cursor, payload
                if is_terminal_event(payload):
                    return


async def get_last_stream_id(run_id: str) -> str:
    """获取当前 Stream 最后一条 id，无数据时返回 `0-0`。"""
    key = stream_key(run_id)
    try:
        client = get_async_client()
        entries = await client.xrevrange(key, count=1)
        if entries:
            return str(entries[0][0])
    except Exception:
        logger.exception({
            "msg": "run_event_last_id_failed",
            "run_id": run_id,
        })
    return "0-0"


def _parse_fields(fields: dict) -> Optional[dict[str, Any]]:
    """解析 Stream entry fields → payload dict。"""
    if not isinstance(fields, dict):
        return None
    raw = fields.get("payload")
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
