"""
    run_task_registry.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    进程内 ChatRun 后台任务注册表（标准版）

    供显式 cancel、防重复启动、幽灵 running 检测使用。
    强版可替换为队列 Worker，业务侧仍通过 execute_run 入口执行。

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.utils.log import logger

# run_id → 正在执行的 asyncio.Task
_tasks: dict[str, asyncio.Task] = {}
# 用户显式取消标记（与 Task.cancel 配合）
_cancel_requested: set[str] = set()


def register_task(run_id: str, task: asyncio.Task) -> None:
    """注册后台任务；同 run_id 已有未完成任务时记录告警并覆盖。"""
    old = _tasks.get(run_id)
    if old is not None and not old.done():
        logger.warning({
            "msg": "run_task_replaced",
            "run_id": run_id,
        })
    _tasks[run_id] = task
    _cancel_requested.discard(run_id)

    def _on_done(t: asyncio.Task) -> None:
        current = _tasks.get(run_id)
        if current is t:
            _tasks.pop(run_id, None)
        _cancel_requested.discard(run_id)

    task.add_done_callback(_on_done)


def get_task(run_id: str) -> Optional[asyncio.Task]:
    """获取 run 对应的后台任务（可能已结束）。"""
    return _tasks.get(run_id)


def has_active_task(run_id: str) -> bool:
    """是否存在未完成的后台任务。"""
    task = _tasks.get(run_id)
    return task is not None and not task.done()


def is_cancel_requested(run_id: str) -> bool:
    """是否已请求取消该 run。"""
    return run_id in _cancel_requested


def request_cancel(run_id: str) -> bool:
    """标记取消并尝试 cancel Task。

    Returns:
        bool -- 是否找到未完成任务并已发出 cancel
    """
    _cancel_requested.add(run_id)
    task = _tasks.get(run_id)
    if task is None or task.done():
        logger.info({
            "msg": "run_cancel_no_active_task",
            "run_id": run_id,
        })
        return False
    task.cancel()
    logger.info({
        "msg": "run_task_cancel_requested",
        "run_id": run_id,
    })
    return True


def list_active_run_ids() -> list[str]:
    """当前进程内未完成的 run_id 列表。"""
    return [rid for rid, t in _tasks.items() if not t.done()]
