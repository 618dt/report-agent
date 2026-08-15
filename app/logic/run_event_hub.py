"""
    run_event_hub.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    进程内 ChatRun 事件扇出（标准版）

    与 Redis Stream 配合：本地 Queue 保证同进程首连/续订不丢事件；
    Redis 负责刷新重连后的回放。

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from app.utils.log import logger

# run_id → 订阅者队列集合
_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


def subscribe(run_id: str, *, maxsize: int = 0) -> asyncio.Queue:
    """注册本地订阅队列。

    maxsize=0 表示无界队列，避免 Redis 变慢时事件被丢弃导致前端只显示部分正文。
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    bind(run_id, queue)
    return queue


def bind(run_id: str, queue: asyncio.Queue) -> None:
    """将已有队列绑定到 run（用于启动前抢先订阅，避免丢 run_started）。"""
    if not run_id or queue is None:
        return
    _subscribers[run_id].add(queue)


def unsubscribe(run_id: str, queue: asyncio.Queue) -> None:
    """取消本地订阅。"""
    subs = _subscribers.get(run_id)
    if not subs:
        return
    subs.discard(queue)
    if not subs:
        _subscribers.pop(run_id, None)


def publish(run_id: str, payload: dict[str, Any]) -> int:
    """向该 run 的所有本地订阅者投递事件。

    Returns:
        int -- 成功投递的队列数量
    """
    if not run_id or not isinstance(payload, dict):
        return 0
    subs = list(_subscribers.get(run_id) or ())
    delivered = 0
    for queue in subs:
        try:
            queue.put_nowait(payload)
            delivered += 1
        except asyncio.QueueFull:
            logger.warning({
                "msg": "run_event_hub_queue_full",
                "run_id": run_id,
                "type": payload.get("type"),
            })
        except Exception:
            logger.exception({
                "msg": "run_event_hub_publish_failed",
                "run_id": run_id,
            })
    return delivered


def subscriber_count(run_id: str) -> int:
    """当前本地订阅者数量。"""
    return len(_subscribers.get(run_id) or ())
