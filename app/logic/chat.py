"""
    chat.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    对话消息业务逻辑：流式对话生成 + HITL 工具审批

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command
from pymongo import ReturnDocument

from app.agent import get_agent
from app.agent.plan_progress import apply_step_status, build_plan_snapshot
from app.agent.tools.local import (
    BEGIN_REPORT_TOOL,
    SUBMIT_REPORT_TOOL,
    UPDATE_PLAN_STEP_TOOL,
)
from app.constants import BizCode
from app.logic import run_event_hub
from app.logic.run_task_registry import (
    has_active_task,
    is_cancel_requested,
    register_task,
    request_cancel,
)
from app.models.chat.chat_model import ChatRun, ChatRunEvent, Conversation, Message
from app.utils.exception_handler import AppException
from app.utils.langfuse_tracing import attach_langfuse_callbacks
from app.utils.log import logger
from app.utils.run_event_bus import (
    get_last_stream_id,
    is_terminal_event,
    iter_live_run_events,
    publish_run_event,
    replay_run_events,
)
from app.utils.text_helper import count_chinese_chars
from app.utils.token_usage import RunUsageTracker, extract_usage_from_message

# 幽灵 running：无本机 Task 且超时后标记失败（报告撰写期也可能进程已死）
_GHOST_RUNNING_TIMEOUT = timedelta(minutes=5)
# partial_content 节流（偏小，刷新后正文更接近实时）
_PARTIAL_CONTENT_MIN_INTERVAL_S = 0.5
_PARTIAL_CONTENT_MIN_CHARS = 40
# partial_report 节流
_PARTIAL_REPORT_MIN_INTERVAL_S = 0.8
_PARTIAL_REPORT_MIN_CHARS = 120

# ---------------------------------------------------------------------------
# 工具输出治理配置
# ---------------------------------------------------------------------------

# 工具输出截断阈值（字节）
_TOOL_OUTPUT_MAX_BYTES = 20480  # 20KB

# 历史回灌限制（业界常见：最近 N 轮 + 字符预算，避免上下文爆炸）
_HYDRATE_MAX_MESSAGES = 20          # 最多回灌条数（约 10 轮 user/assistant）
_HYDRATE_MAX_TOTAL_CHARS = 24000    # 回灌正文总字符上限
_HYDRATE_MAX_MSG_CHARS = 4000       # 单条消息截断长度

# 敏感字段模式（脱敏）
_SENSITIVE_KEY_PATTERN = re.compile(
    r'(api[_-]?key|token|authorization|password|cookie|secret|credential)',
    re.IGNORECASE,
)
_REDACTED_VALUE = "***REDACTED***"


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


async def logic_chat_stream(
    query: str | None = None,
    conversation_id: str = "",
    user_id: str = "",
    approved: bool | None = None,
    run_id: str | None = None,
    response: dict | None = None,
    deep_thinking: bool = False,
    plan_mode: bool = False,
) -> AsyncGenerator[str, None]:
    """统一的流式对话入口：启动后台任务并以订阅者身份输出 SSE。

    生成与 HTTP 连接解耦：客户端断开不会取消 run；
    显式 cancel 或任务异常才会终态化。

    首连先绑定进程内 Queue，再发布 run_started / 启动任务，避免只依赖 Redis 时丢事件。
    """
    # 无界队列：Redis 变慢时也不能把本地事件挤丢
    prebind_queue: asyncio.Queue = asyncio.Queue(maxsize=0)
    started: dict[str, str] | None = None

    try:
        if approved is not None or response is not None:
            started = await logic_start_resume_run(
                conversation_id=conversation_id,
                user_id=user_id,
                approved=approved,
                run_id=run_id,
                response=response,
                prebind_queue=prebind_queue,
            )
        else:
            started = await logic_start_chat_run(
                query=query or "",
                conversation_id=conversation_id,
                user_id=user_id,
                deep_thinking=deep_thinking,
                plan_mode=plan_mode,
                prebind_queue=prebind_queue,
            )

        async for sse_event in logic_subscribe_run_stream(
            run_id=started["run_id"],
            user_id=user_id,
            after_seq=-1,
            prebind_queue=prebind_queue,
        ):
            yield sse_event
    finally:
        if started and started.get("run_id"):
            run_event_hub.unsubscribe(started["run_id"], prebind_queue)


async def logic_start_chat_run(
    query: str,
    conversation_id: str = "",
    user_id: str = "",
    deep_thinking: bool = False,
    plan_mode: bool = False,
    prebind_queue: asyncio.Queue | None = None,
) -> dict[str, str]:
    """创建新消息 run，发布 run_started，并启动后台 execute_run。

    Arguments:
        prebind_queue -- 若提供，在发布任何事件前绑定到本地 hub，保证首连不丢事件

    Returns:
        dict -- {run_id, conversation_id}
    """
    conversation_id = await _resolve_conversation(query, conversation_id, user_id)

    now = datetime.now(timezone.utc)
    run = ChatRun(
        conversation_id=conversation_id,
        user_message_id="",
        owner_id=user_id,
        status=ChatRun.StatusField.RUNNING,
        deep_thinking=bool(deep_thinking),
        plan_mode=bool(plan_mode),
        plan_confirmed=False,
        started_at=now,
        partial_content="",
        partial_report=None,
        last_seq=0,
    )
    run_id = run._id
    await ChatRun.a_p_col.insert_one(run.to_dict())
    logger.info({
        "msg": "chat_run_created",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "deep_thinking": bool(deep_thinking),
        "plan_mode": bool(plan_mode),
    })

    user_msg = Message(
        conversation_id=conversation_id,
        sender_id=user_id,
        receiver_id="agent",
        msg_type=Message.MsgTypeField.TEXT,
        content=query,
        status=Message.StatusField.SUCCESS,
        run_id=run_id,
    )
    await Message.a_p_col.insert_one(user_msg.to_dict())
    logger.info({
        "msg": "user_message_saved",
        "conversation_id": conversation_id,
        "message_id": user_msg._id,
        "run_id": run_id,
    })

    await ChatRun.a_p_col.update_one(
        {"_id": run_id},
        {"$set": {"user_message_id": user_msg._id, "update_time": now}},
    )

    # 先绑定本地订阅，再发 run_started，避免首事件只进 Redis、订阅尚未建立
    if prebind_queue is not None:
        run_event_hub.bind(run_id, prebind_queue)

    await _publish_sse_payload(
        _sse_payload("run_started", conversation_id, run_id, 0),
    )

    agent = get_agent()
    config = {
        "configurable": {
            "thread_id": conversation_id,
            "user_id": user_id,
            "run_id": run_id,
            "deep_thinking": bool(deep_thinking),
            "plan_mode": bool(plan_mode),
            "plan_confirmed": False,
        },
    }
    input_data = {"messages": [{"role": "user", "content": query}]}

    async def _setup_and_run() -> None:
        try:
            await _auto_clear_stale_interrupt(agent, config, conversation_id)
            await _hydrate_thread_from_messages(
                agent=agent,
                config=config,
                conversation_id=conversation_id,
                exclude_message_id=user_msg._id,
            )
            await execute_run(
                agent=agent,
                input_data=input_data,
                config=config,
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                trace_name="chat-response",
            )
        except Exception as e:
            logger.exception({
                "msg": "chat_stream_setup_error",
                "conversation_id": conversation_id,
                "run_id": run_id,
            })
            await _update_run_status(
                run_id,
                ChatRun.StatusField.FAILED,
                error={"code": "stream_setup_error", "message": str(e)},
            )
            seq = await _get_next_seq(run_id)
            await _publish_sse_payload(
                _sse_payload(
                    "error", conversation_id, run_id, seq,
                    message="Agent stream setup error",
                ),
            )

    task = asyncio.create_task(_setup_and_run(), name=f"chat-run-{run_id}")
    register_task(run_id, task)
    return {"run_id": run_id, "conversation_id": conversation_id}


async def logic_start_resume_run(
    conversation_id: str,
    user_id: str,
    approved: bool | None = None,
    run_id: str | None = None,
    response: dict | None = None,
    prebind_queue: asyncio.Queue | None = None,
) -> dict[str, str]:
    """准备 HITL 恢复：写 approval/快照事件，启动后台 resume 执行。"""
    hitl_response = _normalize_hitl_response(approved=approved, response=response)

    if run_id:
        run_doc = await ChatRun.a_p_col.find_one({
            "_id": run_id,
            "owner_id": user_id,
            "is_deleted": 0,
        })
        if not run_doc:
            raise AppException(
                message="Run 不存在或已删除",
                code=BizCode.NOT_FOUND,
                status_code=404,
            )
        if run_doc.get("status") != ChatRun.StatusField.INTERRUPTED:
            raise AppException(
                message="Run 状态不是 interrupted，无法恢复",
                code=BizCode.BUSINESS_ERROR,
                status_code=400,
            )
    else:
        run_doc = await ChatRun.a_p_col.find_one({
            "conversation_id": conversation_id,
            "owner_id": user_id,
            "status": ChatRun.StatusField.INTERRUPTED,
            "is_deleted": 0,
        }, sort=[("update_time", -1)])
        if not run_doc:
            raise AppException(
                message="没有找到待恢复的中断 Run",
                code=BizCode.NOT_FOUND,
                status_code=404,
            )
        run_id = run_doc["_id"]

    if has_active_task(run_id):
        raise AppException(
            message="Run 正在执行中，请勿重复恢复",
            code=BizCode.BUSINESS_ERROR,
            status_code=400,
        )

    logger.info({
        "msg": "resume_chat_run",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "action": hitl_response.get("action"),
    })

    if prebind_queue is not None:
        run_event_hub.bind(run_id, prebind_queue)

    last_interrupt = await ChatRunEvent.a_p_col.find_one(
        {
            "run_id": run_id,
            "type": ChatRunEvent.TypeField.INTERRUPT,
            "is_deleted": 0,
        },
        sort=[("seq", -1)],
    )
    interrupt_payload = (last_interrupt or {}).get("payload") or {}
    interrupt_reason = str(interrupt_payload.get("reason") or "")
    interrupt_schema = interrupt_payload.get("schema") or {}
    if not isinstance(interrupt_schema, dict):
        interrupt_schema = {}

    now = datetime.now(timezone.utc)
    next_seq = await _get_next_seq(run_id)
    approval_payload = _build_approval_payload(
        hitl_response=hitl_response,
        interrupt_payload=interrupt_payload,
        interrupt_seq=(last_interrupt or {}).get("seq"),
    )
    approval_event = ChatRunEvent(
        conversation_id=conversation_id,
        run_id=run_id,
        seq=next_seq,
        type=ChatRunEvent.TypeField.APPROVAL,
        payload=approval_payload,
    )
    await ChatRunEvent.a_p_col.insert_one(approval_event.to_dict())
    await _publish_sse_payload(
        _sse_payload(
            "approval", conversation_id, run_id, next_seq,
            data=approval_payload,
        ),
    )
    logger.info({
        "msg": "hitl_approval_persisted",
        "run_id": run_id,
        "conversation_id": conversation_id,
        "action": approval_payload.get("action"),
        "reason": approval_payload.get("reason"),
        "interrupt_seq": approval_payload.get("interrupt_seq"),
    })

    await ChatRun.a_p_col.update_one(
        {"_id": run_id},
        {"$set": {
            "status": ChatRun.StatusField.RUNNING,
            "update_time": now,
        }},
    )

    agent = get_agent()
    deep_thinking = bool(run_doc.get("deep_thinking", False))
    plan_mode = bool(run_doc.get("plan_mode", False))
    plan_confirmed = bool(run_doc.get("plan_confirmed", False))
    action = hitl_response.get("action")
    hitl_payload = hitl_response.get("payload") or {}
    if not isinstance(hitl_payload, dict):
        hitl_payload = {}

    if (
        plan_mode
        and not plan_confirmed
        and action == "confirm"
        and interrupt_reason == "plan_confirm"
    ):
        steps = hitl_payload.get("steps")
        if not isinstance(steps, list):
            steps = interrupt_schema.get("steps") or []
        plan_snapshot = build_plan_snapshot(
            title=str(
                interrupt_payload.get("title")
                or interrupt_schema.get("title")
                or ""
            ),
            goal=str(interrupt_schema.get("goal") or ""),
            steps=steps,
            risks=interrupt_schema.get("risks"),
            assumptions=interrupt_schema.get("assumptions"),
        )
        plan_confirmed = True
        await ChatRun.a_p_col.update_one(
            {"_id": run_id},
            {"$set": {
                "plan_confirmed": True,
                "plan": plan_snapshot,
                "update_time": now,
            }},
        )
        plan_sse = await _emit_plan_event(
            conversation_id=conversation_id,
            run_id=run_id,
            plan=plan_snapshot,
        )
        await _publish_sse_string(run_id, plan_sse)
        logger.info({
            "msg": "plan_confirmed",
            "run_id": run_id,
            "conversation_id": conversation_id,
            "steps_count": plan_snapshot.get("total_count"),
        })

    if interrupt_reason == "outline_confirm" and action == "confirm":
        chapters = hitl_payload.get("chapters")
        if not isinstance(chapters, list):
            chapters = interrupt_schema.get("chapters") or []
        outline_snapshot = _build_outline_snapshot(
            title=str(
                interrupt_payload.get("title")
                or interrupt_schema.get("title")
                or ""
            ),
            topic=str(interrupt_schema.get("topic") or ""),
            chapters=chapters,
            action=action,
        )
        await ChatRun.a_p_col.update_one(
            {"_id": run_id},
            {"$set": {
                "outline": outline_snapshot,
                "update_time": now,
            }},
        )
        outline_sse = await _emit_outline_event(
            conversation_id=conversation_id,
            run_id=run_id,
            outline=outline_snapshot,
        )
        await _publish_sse_string(run_id, outline_sse)
        logger.info({
            "msg": "outline_confirmed",
            "run_id": run_id,
            "conversation_id": conversation_id,
            "chapters_count": outline_snapshot.get("total_count"),
            "selected_count": outline_snapshot.get("selected_count"),
        })

    config = {
        "configurable": {
            "thread_id": conversation_id,
            "user_id": user_id,
            "run_id": run_id,
            "deep_thinking": deep_thinking,
            "plan_mode": plan_mode,
            "plan_confirmed": plan_confirmed,
        },
    }

    if action == "deny":
        await _clear_pending_tool_calls(agent, config)
        logger.info({
            "msg": "tool_calls_denied",
            "conversation_id": conversation_id,
            "run_id": run_id,
        })

    command = Command(resume=hitl_response)

    async def _resume_and_run() -> None:
        await execute_run(
            agent=agent,
            input_data=command,
            config=config,
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=run_id,
            trace_name="chat-resume",
        )

    task = asyncio.create_task(_resume_and_run(), name=f"chat-resume-{run_id}")
    register_task(run_id, task)
    return {"run_id": run_id, "conversation_id": conversation_id}


async def logic_chat_resume_stream(
    conversation_id: str,
    user_id: str,
    approved: bool | None = None,
    run_id: str | None = None,
    response: dict | None = None,
) -> AsyncGenerator[str, None]:
    """兼容旧调用：启动 resume 后台任务并订阅 SSE。"""
    prebind_queue: asyncio.Queue = asyncio.Queue(maxsize=0)
    started = await logic_start_resume_run(
        conversation_id=conversation_id,
        user_id=user_id,
        approved=approved,
        run_id=run_id,
        response=response,
        prebind_queue=prebind_queue,
    )
    try:
        async for sse_event in logic_subscribe_run_stream(
            run_id=started["run_id"],
            user_id=user_id,
            after_seq=-1,
            prebind_queue=prebind_queue,
        ):
            yield sse_event
    finally:
        run_event_hub.unsubscribe(started["run_id"], prebind_queue)


async def execute_run(
    agent,
    input_data,
    config: dict,
    conversation_id: str,
    user_id: str,
    run_id: str,
    trace_name: str = "chat-response",
) -> None:
    """后台执行 Agent 流：消费事件并写入 Redis（供订阅端扇出）。"""
    partial_parts: list[str] = []
    last_partial_flush = 0.0
    chars_since_flush = 0
    report_markdown = ""
    report_meta: dict[str, Any] = {}
    last_report_flush = 0.0
    report_chars_since_flush = 0

    try:
        async for sse_event in _agent_stream_events(
            agent=agent,
            input_data=input_data,
            config=config,
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=run_id,
            trace_name=trace_name,
        ):
            payload = _parse_sse_payload(sse_event)
            if payload is None:
                continue
            await _publish_sse_payload(payload)

            if payload.get("type") == "content" and payload.get("content"):
                chunk = str(payload["content"])
                partial_parts.append(chunk)
                chars_since_flush += len(chunk)
                now_ts = time.monotonic()
                # 节流写快照；阈值较小以便刷新后正文更接近实时
                if (
                    chars_since_flush >= _PARTIAL_CONTENT_MIN_CHARS
                    or (now_ts - last_partial_flush) >= _PARTIAL_CONTENT_MIN_INTERVAL_S
                    or last_partial_flush == 0.0
                ):
                    await _persist_partial_content(run_id, "".join(partial_parts))
                    last_partial_flush = now_ts
                    chars_since_flush = 0

            # 报告流：artifact_* 不落库事件表，需节流写入 partial_report 供刷新恢复
            evt_type = payload.get("type")
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            try:
                evt_seq = int(payload["seq"]) if payload.get("seq") is not None else 0
            except (TypeError, ValueError):
                evt_seq = 0
            if evt_type == "artifact_start":
                report_meta = {
                    "tool_call_id": str(data.get("tool_call_id") or ""),
                    "title": str(data.get("title") or ""),
                    "topic": str(data.get("topic") or ""),
                    "status": "generating",
                    "last_seq": evt_seq,
                }
                report_markdown = ""
                report_chars_since_flush = 0
                await _persist_partial_report(
                    run_id,
                    {**report_meta, "markdown": ""},
                )
                last_report_flush = time.monotonic()
            elif evt_type == "artifact_delta" and data.get("delta"):
                delta = str(data["delta"])
                report_markdown += delta
                report_chars_since_flush += len(delta)
                if data.get("tool_call_id"):
                    report_meta["tool_call_id"] = str(data["tool_call_id"])
                if data.get("title"):
                    report_meta["title"] = str(data["title"])
                if data.get("topic"):
                    report_meta["topic"] = str(data["topic"])
                report_meta["status"] = "generating"
                report_meta["last_seq"] = evt_seq
                now_ts = time.monotonic()
                if (
                    report_chars_since_flush >= _PARTIAL_REPORT_MIN_CHARS
                    or (now_ts - last_report_flush) >= _PARTIAL_REPORT_MIN_INTERVAL_S
                    or last_report_flush == 0.0
                ):
                    await _persist_partial_report(
                        run_id,
                        {**report_meta, "markdown": report_markdown},
                    )
                    last_report_flush = now_ts
                    report_chars_since_flush = 0
            elif evt_type == "artifact":
                report_meta = {
                    "tool_call_id": str(data.get("tool_call_id") or ""),
                    "title": str(data.get("title") or ""),
                    "topic": str(data.get("topic") or ""),
                    "status": "ready",
                    "last_seq": evt_seq,
                }
                report_markdown = str(data.get("markdown") or report_markdown)
                await _persist_partial_report(
                    run_id,
                    {**report_meta, "markdown": report_markdown},
                )
                last_report_flush = time.monotonic()
                report_chars_since_flush = 0
    except asyncio.CancelledError:
        if is_cancel_requested(run_id):
            logger.info({
                "msg": "execute_run_cancelled",
                "run_id": run_id,
                "conversation_id": conversation_id,
            })
        else:
            logger.warning({
                "msg": "execute_run_task_cancelled_unexpected",
                "run_id": run_id,
                "conversation_id": conversation_id,
            })
        raise
    finally:
        if partial_parts:
            try:
                await _persist_partial_content(run_id, "".join(partial_parts))
            except Exception:
                logger.exception({
                    "msg": "partial_content_final_flush_failed",
                    "run_id": run_id,
                })
        if report_meta.get("tool_call_id") or report_markdown:
            try:
                await _persist_partial_report(
                    run_id,
                    {
                        **report_meta,
                        "markdown": report_markdown,
                        "status": report_meta.get("status") or "generating",
                    },
                )
            except Exception:
                logger.exception({
                    "msg": "partial_report_final_flush_failed",
                    "run_id": run_id,
                })


def _should_emit_payload(
    payload: dict[str, Any],
    *,
    after_seq: int,
    emitted_seqs: set[int],
) -> tuple[bool, bool]:
    """判断是否应下发事件。

    Returns:
        (should_yield, is_duplicate_terminal)
    """
    seq = payload.get("seq")
    try:
        seq_int = int(seq) if seq is not None else -1
    except (TypeError, ValueError):
        seq_int = -1
    if seq_int >= 0 and (seq_int <= after_seq or seq_int in emitted_seqs):
        return False, is_terminal_event(payload)
    if seq_int >= 0:
        emitted_seqs.add(seq_int)
    return True, False


async def _drain_local_queue(
    queue: asyncio.Queue,
    *,
    after_seq: int,
    emitted_seqs: set[int],
) -> AsyncGenerator[tuple[str, bool], None]:
    """非阻塞排空本地队列。

    Yields:
        (sse_string, is_terminal)

    去重掉的终态不在此标记 terminal，留给调用方按 Mongo 状态补发，
    避免「空 yield + return」导致前端永远收不到 done。
    """
    while True:
        try:
            payload = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        ok, _dup_term = _should_emit_payload(
            payload, after_seq=after_seq, emitted_seqs=emitted_seqs,
        )
        if not ok:
            continue
        terminal = is_terminal_event(payload)
        yield _format_sse_from_payload(payload), terminal
        if terminal:
            return


async def _synthesize_terminal_sse(
    run_doc: dict,
    conversation_id: str,
    run_id: str,
) -> str | None:
    """Mongo 已终态但队列未收到 done/error/cancelled 时，补发终态事件。"""
    status = run_doc.get("status")
    usage = run_doc.get("usage")
    last_seq = int(run_doc.get("last_seq") or 0)
    if status == ChatRun.StatusField.SUCCESS:
        payload = _sse_payload(
            "done", conversation_id, run_id, last_seq,
            usage=usage,
        )
        msg_id = run_doc.get("assistant_message_id") or ""
        if msg_id:
            payload["message_id"] = msg_id
        return _format_sse_from_payload(payload)
    if status == ChatRun.StatusField.FAILED:
        err = run_doc.get("error") or {}
        return _format_sse_from_payload(
            _sse_payload(
                "error", conversation_id, run_id, last_seq,
                message=err.get("message") or "run failed",
                usage=usage,
                message_id=run_doc.get("assistant_message_id") or None,
            ),
        )
    if status == ChatRun.StatusField.CANCELLED:
        return _format_sse_from_payload(
            _sse_payload(
                "cancelled", conversation_id, run_id, last_seq,
                message="cancelled",
                usage=usage,
            ),
        )
    if status == ChatRun.StatusField.INTERRUPTED:
        return _format_sse_from_payload(
            _sse_payload(
                "interrupted", conversation_id, run_id, last_seq,
                usage=usage,
            ),
        )
    return None


async def logic_subscribe_run_stream(
    run_id: str,
    user_id: str,
    after_seq: int = -1,
    prebind_queue: asyncio.Queue | None = None,
) -> AsyncGenerator[str, None]:
    """订阅 run 事件流：本地 Queue 优先，Redis 仅用于刷新续订回放。

    客户端断开只结束本生成器，不影响后台 execute_run。
    首连（带 prebind_queue）完全走本地扇出，不因 Redis 慢/失败而卡顿。
    """
    run_doc = await _get_run_owned_by(run_id, user_id)
    run_doc = await _maybe_fail_ghost_running(run_doc)

    conversation_id = run_doc.get("conversation_id", "")
    status = run_doc.get("status", "")
    emitted_seqs: set[int] = set()
    # 首连已绑定本地队列，无需再依赖 Redis 回放
    first_connection = prebind_queue is not None

    owns_queue = prebind_queue is None
    queue = prebind_queue if prebind_queue is not None else run_event_hub.subscribe(run_id)
    last_id = "0-0"

    try:
        # 1) Mongo 离散事件回放（工具/计划等；content 一般不在此）
        if not first_connection:
            mongo_cursor = ChatRunEvent.a_p_col.find({
                "run_id": run_id,
                "is_deleted": 0,
                "seq": {"$gt": after_seq},
            }).sort([("seq", 1)])
            mongo_docs = await mongo_cursor.to_list(length=2000)
            for doc in mongo_docs:
                payload = _mongo_event_to_sse_payload(doc, conversation_id)
                if payload is None:
                    continue
                ok, _dup = _should_emit_payload(
                    payload, after_seq=after_seq, emitted_seqs=emitted_seqs,
                )
                if not ok:
                    continue
                yield _format_sse_from_payload(payload)
                if is_terminal_event(payload):
                    return

        # 2) 非首连：带超时的 Redis 回放
        if not first_connection:
            try:
                replayed = await asyncio.wait_for(
                    replay_run_events(run_id, after_seq=after_seq),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                logger.warning({
                    "msg": "run_event_replay_timeout",
                    "run_id": run_id,
                })
                replayed = []
            if replayed:
                last_id = replayed[-1][0]
            else:
                try:
                    last_id = await asyncio.wait_for(
                        get_last_stream_id(run_id),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    last_id = "0-0"

            for _entry_id, payload in replayed:
                ok, _dup = _should_emit_payload(
                    payload, after_seq=after_seq, emitted_seqs=emitted_seqs,
                )
                if not ok:
                    continue
                yield _format_sse_from_payload(payload)
                if is_terminal_event(payload):
                    return

        # 3) 排空本地队列
        async for sse, terminal in _drain_local_queue(
            queue, after_seq=after_seq, emitted_seqs=emitted_seqs,
        ):
            if sse:
                yield sse
            if terminal:
                return

        # 4) 若已终态：再排空一次 + 必要时补发终态，绝不能裸 return（否则前端 loading 挂死）
        run_doc = await ChatRun.a_p_col.find_one({"_id": run_id, "is_deleted": 0}) or run_doc
        status = run_doc.get("status", status)
        if status in (
            ChatRun.StatusField.SUCCESS,
            ChatRun.StatusField.FAILED,
            ChatRun.StatusField.CANCELLED,
            ChatRun.StatusField.INTERRUPTED,
        ):
            async for sse, terminal in _drain_local_queue(
                queue, after_seq=after_seq, emitted_seqs=emitted_seqs,
            ):
                if sse:
                    yield sse
                if terminal:
                    return
            synthetic = await _synthesize_terminal_sse(
                run_doc, conversation_id, run_id,
            )
            if synthetic:
                yield synthetic
            return

        # 5) 实时：本地 Queue；任务结束后 Redis XREAD 补尾（非首连）
        use_local = (
            first_connection
            or has_active_task(run_id)
            or run_event_hub.subscriber_count(run_id) > 0
        )
        if use_local:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # 超时先排空（防止漏掉刚入队的 content/done）
                    drained_terminal = False
                    async for sse, terminal in _drain_local_queue(
                        queue, after_seq=after_seq, emitted_seqs=emitted_seqs,
                    ):
                        if sse:
                            yield sse
                        if terminal:
                            drained_terminal = True
                            break
                    if drained_terminal:
                        return

                    run_doc = await ChatRun.a_p_col.find_one(
                        {"_id": run_id, "is_deleted": 0},
                    ) or run_doc
                    status = run_doc.get("status", "")
                    if status in (
                        ChatRun.StatusField.SUCCESS,
                        ChatRun.StatusField.FAILED,
                        ChatRun.StatusField.CANCELLED,
                        ChatRun.StatusField.INTERRUPTED,
                    ):
                        async for sse, terminal in _drain_local_queue(
                            queue, after_seq=after_seq, emitted_seqs=emitted_seqs,
                        ):
                            if sse:
                                yield sse
                            if terminal:
                                return
                        synthetic = await _synthesize_terminal_sse(
                            run_doc, conversation_id, run_id,
                        )
                        if synthetic:
                            yield synthetic
                        return
                    if not has_active_task(run_id) and not first_connection:
                        break
                    continue

                ok, dup_term = _should_emit_payload(
                    payload, after_seq=after_seq, emitted_seqs=emitted_seqs,
                )
                if not ok:
                    if dup_term:
                        # seq 去重误伤终态时补发，避免前端 loading 挂死
                        run_doc = await ChatRun.a_p_col.find_one(
                            {"_id": run_id, "is_deleted": 0},
                        ) or run_doc
                        synthetic = await _synthesize_terminal_sse(
                            run_doc, conversation_id, run_id,
                        )
                        if synthetic:
                            yield synthetic
                        return
                    continue
                yield _format_sse_from_payload(payload)
                if is_terminal_event(payload):
                    return

        if first_connection:
            # 首连收尾：再排空并按 Mongo 状态补终态
            async for sse, terminal in _drain_local_queue(
                queue, after_seq=after_seq, emitted_seqs=emitted_seqs,
            ):
                if sse:
                    yield sse
                if terminal:
                    return
            run_doc = await ChatRun.a_p_col.find_one(
                {"_id": run_id, "is_deleted": 0},
            ) or run_doc
            synthetic = await _synthesize_terminal_sse(
                run_doc, conversation_id, run_id,
            )
            if synthetic:
                yield synthetic
            return

        async for _entry_id, payload in iter_live_run_events(run_id, last_id=last_id):
            ok, dup_term = _should_emit_payload(
                payload, after_seq=after_seq, emitted_seqs=emitted_seqs,
            )
            if not ok:
                if dup_term:
                    run_doc = await ChatRun.a_p_col.find_one(
                        {"_id": run_id, "is_deleted": 0},
                    ) or run_doc
                    synthetic = await _synthesize_terminal_sse(
                        run_doc, conversation_id, run_id,
                    )
                    if synthetic:
                        yield synthetic
                    return
                continue
            yield _format_sse_from_payload(payload)
            if is_terminal_event(payload):
                return
    finally:
        if owns_queue:
            run_event_hub.unsubscribe(run_id, queue)


async def logic_cancel_run(run_id: str, user_id: str) -> dict:
    """显式取消正在执行的 run。"""
    run_doc = await _get_run_owned_by(run_id, user_id)
    status = run_doc.get("status")
    conversation_id = run_doc.get("conversation_id", "")

    if status in (
        ChatRun.StatusField.SUCCESS,
        ChatRun.StatusField.FAILED,
        ChatRun.StatusField.CANCELLED,
    ):
        return {
            "run_id": run_id,
            "status": status,
            "cancelled": False,
            "message": "run already finished",
        }

    cancelled_task = request_cancel(run_id)

    if status == ChatRun.StatusField.INTERRUPTED or not cancelled_task:
        # 无活跃任务（含 interrupted 等待确认）：直接标 cancelled
        await _update_run_status(
            run_id,
            ChatRun.StatusField.CANCELLED,
            error={"code": "user_cancelled", "message": "cancelled by user"},
        )
        seq = await _get_next_seq(run_id)
        await _publish_sse_payload(
            _sse_payload(
                "cancelled", conversation_id, run_id, seq,
                message="cancelled by user",
            ),
        )
        logger.info({
            "msg": "chat_run_cancelled_by_user",
            "run_id": run_id,
            "had_task": cancelled_task,
            "prev_status": status,
        })
        return {
            "run_id": run_id,
            "status": ChatRun.StatusField.CANCELLED,
            "cancelled": True,
        }

    # 有活跃任务：等待其 CancelledError 路径写终态；超时则兜底
    for _ in range(50):
        await asyncio.sleep(0.1)
        doc = await ChatRun.a_p_col.find_one({"_id": run_id}, {"status": 1})
        if doc and doc.get("status") == ChatRun.StatusField.CANCELLED:
            return {
                "run_id": run_id,
                "status": ChatRun.StatusField.CANCELLED,
                "cancelled": True,
            }
        if not has_active_task(run_id):
            break

    doc = await ChatRun.a_p_col.find_one({"_id": run_id}, {"status": 1})
    cur = (doc or {}).get("status")
    if cur != ChatRun.StatusField.CANCELLED:
        await _update_run_status(
            run_id,
            ChatRun.StatusField.CANCELLED,
            error={"code": "user_cancelled", "message": "cancelled by user"},
        )
        seq = await _get_next_seq(run_id)
        await _publish_sse_payload(
            _sse_payload(
                "cancelled", conversation_id, run_id, seq,
                message="cancelled by user",
            ),
        )
    return {
        "run_id": run_id,
        "status": ChatRun.StatusField.CANCELLED,
        "cancelled": True,
    }


def _normalize_hitl_response(
    approved: bool | None = None,
    response: dict | None = None,
) -> dict:
    """将 approved / response 归一化为统一 HITL 响应字典"""
    if response and isinstance(response, dict) and response.get("action"):
        action = str(response["action"]).strip().lower()
        payload = response.get("payload")
        if payload is not None and not isinstance(payload, dict):
            payload = {"value": payload}
        return {
            "action": action,
            "payload": payload if isinstance(payload, dict) else None,
        }

    if approved is not None:
        return {
            "action": "approve" if approved else "deny",
            "payload": None,
        }

    raise AppException(
        message="恢复中断时必须提供 approved 或 response",
        code=BizCode.PARAM_ERROR,
        status_code=400,
    )


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


async def _agent_stream_events(
    agent,
    input_data,
    config: dict,
    conversation_id: str,
    user_id: str,
    run_id: str,
    trace_name: str = "chat-response",
) -> AsyncGenerator[str, None]:
    """Agent 流式事件处理器（共用）

    处理 astream 的 messages/updates 事件：
    - messages: 提取文本增量 → "content"；若处于报告正文模式则改为
      "artifact_delta"；并尝试从 tool_call_chunks 解析 submit 参数
    - updates: 检测中断；begin_report → artifact_start；
      submit_report → 终态 artifact；工具调用/响应事件
    由 logic_chat_stream 和 logic_chat_resume_stream 共用。

    所有 SSE 事件统一携带 conversation_id, run_id, seq。
    工具调用/工具结果/中断/终态 artifact 写入 chat_run_event；
    artifact_start/delta 仅下发不落库。
    token 用量：流式过程启发式估算 output，收到 usage_metadata 后校正并累加。

    Arguments:
        agent -- LangGraph CompiledStateGraph
        input_data -- astream 的输入（消息 dict 或 Command）
        config -- Agent 配置
        conversation_id -- 会话 ID
        user_id -- 用户 ID
        run_id -- 当前 run 的 ID
        trace_name -- Langfuse 根 run 名称（chat-response / chat-resume）
    """
    assistant_content_parts: list[str] = []
    last_full_content: str = ""
    last_reasoning: str = ""
    interrupted: bool = False
    # 标记是否已写入终态（成功/失败/中断/显式取消）
    status_finalized: bool = False
    report_tracker = _SubmitReportStreamTracker()
    usage_tracker = await _load_run_usage_tracker(run_id)

    configurable = (config or {}).get("configurable") or {}
    tags = ["chat", "report-agent"]
    if configurable.get("plan_mode"):
        tags.append("plan-mode")
    if configurable.get("deep_thinking"):
        tags.append("deep-thinking")
    if trace_name == "chat-resume":
        tags.append("hitl-resume")

    # Langfuse：一轮对话 = 一条 trace；conversation_id 作为 session_id 串联多轮
    config = attach_langfuse_callbacks(
        config,
        trace_name=trace_name,
        user_id=user_id,
        session_id=conversation_id,
        tags=tags,
        metadata={
            "run_id": run_id,
            "conversation_id": conversation_id,
            "deep_thinking": bool(configurable.get("deep_thinking")),
            "plan_mode": bool(configurable.get("plan_mode")),
            "feature": "chat",
        },
    )

    try:
        async for event in agent.astream(
            input_data,
            config=config,
            stream_mode=["messages", "updates"],
            subgraphs=True,
            version="v2",
        ):
            event_type = event.get("type") if isinstance(event, dict) else None

            if event_type == "messages":
                data = event.get("data")
                if isinstance(data, tuple) and len(data) >= 1:
                    chunk = data[0]
                    if isinstance(chunk, (AIMessage, AIMessageChunk)):
                        # ---- 思考增量 ----
                        reasoning_text = _extract_reasoning_content(chunk)
                        if reasoning_text:
                            if reasoning_text.startswith(last_reasoning):
                                reasoning_delta = reasoning_text[len(last_reasoning):]
                            else:
                                reasoning_delta = reasoning_text
                            last_reasoning = reasoning_text
                            if reasoning_delta:
                                # 流式增量不落库、不占 seq（终态由 model 节点落库）
                                yield _make_sse(
                                    "reasoning", conversation_id, run_id, -1,
                                    data={"delta": reasoning_delta},
                                )
                                if usage_tracker.add_reasoning_delta(reasoning_delta):
                                    yield _make_usage_sse(
                                        conversation_id, run_id, usage_tracker,
                                    )

                        raw_content = chunk.content
                        full_content = _normalize_message_content(raw_content)
                        if full_content:
                            if full_content.startswith(last_full_content):
                                delta = full_content[len(last_full_content):]
                            else:
                                delta = full_content
                            last_full_content = full_content
                            if delta:
                                if report_tracker.content_mode:
                                    # 报告正文：走 artifact 流，不进聊天气泡
                                    async for sse in report_tracker.ingest_content_delta(
                                        delta,
                                        conversation_id=conversation_id,
                                        run_id=run_id,
                                    ):
                                        yield sse
                                else:
                                    assistant_content_parts.append(delta)
                                    seq = await _get_next_seq(run_id)
                                    yield _make_sse(
                                        "content", conversation_id, run_id, seq,
                                        content=delta,
                                    )
                                if usage_tracker.add_output_delta(delta):
                                    yield _make_usage_sse(
                                        conversation_id, run_id, usage_tracker,
                                    )

                        # API 权威 usage（流式末包，可能 choices 为空）
                        api_usage = extract_usage_from_message(chunk)
                        if api_usage and usage_tracker.commit_usage(
                            api_usage,
                            model=_extract_model_name(chunk),
                        ):
                            yield _make_usage_sse(
                                conversation_id, run_id, usage_tracker,
                            )
                            await _persist_run_usage(
                                run_id, usage_tracker.final_snapshot(),
                            )

                        if isinstance(chunk, AIMessageChunk):
                            async for sse in report_tracker.ingest(
                                chunk,
                                conversation_id=conversation_id,
                                run_id=run_id,
                            ):
                                yield sse

            elif event_type == "updates":
                # 终态 tool_call 前冲刷未下发的报告增量
                async for sse in report_tracker.flush_all(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    force=True,
                ):
                    yield sse
                data = event.get("data", {})
                if isinstance(data, dict):
                    # 检测中断（保留，供后续 HITL 启用时使用）
                    if "__interrupt__" in data:
                        interrupted = True
                    # 处理节点输出：从 "model"/"tools" 节点提取工具调用和响应
                    for source, update in data.items():
                        if source in ("model", "tools"):
                            messages = update.get("messages", [])
                            if messages:
                                last_msg = messages[-1]
                                # model 节点：落库完整思考段（若有）
                                if source == "model":
                                    async for sse in _emit_reasoning_event(
                                        last_msg,
                                        conversation_id=conversation_id,
                                        run_id=run_id,
                                    ):
                                        yield sse
                                    # 流式未带 usage 时，用终态消息兜底校正
                                    api_usage = extract_usage_from_message(last_msg)
                                    if api_usage and usage_tracker.commit_usage(
                                        api_usage,
                                        model=_extract_model_name(last_msg),
                                    ):
                                        yield _make_usage_sse(
                                            conversation_id, run_id, usage_tracker,
                                        )
                                        await _persist_run_usage(
                                            run_id, usage_tracker.final_snapshot(),
                                        )
                                sse_list = await _format_node_message_event(
                                    source, last_msg,
                                    conversation_id, run_id,
                                    report_tracker=report_tracker,
                                )
                                for sse in sse_list:
                                    yield sse
                                # 新的模型轮次开始时重置 content / reasoning 累积基准，
                                # 避免把上一轮正文拼进本轮 delta
                                if source == "model":
                                    last_full_content = ""
                                    last_reasoning = ""
                                    usage_tracker.begin_turn()

        # ---- 处理中断：提取结构化 interrupt payload ----
        if interrupted:
            report_tracker.deactivate_content_mode()
            usage_snapshot = usage_tracker.final_snapshot()
            await _persist_run_usage(run_id, usage_snapshot)
            await _handle_interrupt(agent, config, conversation_id, run_id)
            status_finalized = True
            interrupt_payload = await _extract_interrupt_payload(agent, config)
            seq = await _get_next_seq(run_id)
            # 保存 interrupt 事件
            interrupt_event = ChatRunEvent(
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq,
                type=ChatRunEvent.TypeField.INTERRUPT,
                payload=interrupt_payload,
            )
            await ChatRunEvent.a_p_col.insert_one(interrupt_event.to_dict())

            yield _make_sse(
                "interrupt", conversation_id, run_id, seq,
                data=interrupt_payload,
            )
            yield _make_usage_sse(conversation_id, run_id, usage_tracker)
            seq = await _get_next_seq(run_id)
            yield _make_sse(
                "interrupted", conversation_id, run_id, seq,
                usage=usage_snapshot,
            )
            logger.info({
                "msg": "agent_interrupted_for_hitl",
                "conversation_id": conversation_id,
                "run_id": run_id,
                "reason": interrupt_payload.get("reason"),
                "tool_calls_count": len(interrupt_payload.get("tool_calls") or []),
                "usage": usage_snapshot,
            })
            return

        # ---- 正常完成：保存助手消息到 MongoDB ----
        assistant_content = "".join(assistant_content_parts)
        assistant_msg_id = await _persist_assistant_message(
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=run_id,
            content=assistant_content,
            status=Message.StatusField.SUCCESS,
            skip_if_empty=True,
        )

        usage_snapshot = usage_tracker.final_snapshot()

        # 回填 events 的 parent_message_id
        if assistant_msg_id:
            await ChatRunEvent.a_p_col.update_many(
                {"run_id": run_id, "is_deleted": 0},
                {"$set": {"parent_message_id": assistant_msg_id}},
            )

        # 先下发 done，再标 SUCCESS，避免订阅端读到 SUCCESS 却漏掉 done
        seq = await _get_next_seq(run_id)
        done_data = {
            "type": "done",
            "conversation_id": conversation_id,
            "run_id": run_id,
            "seq": seq,
            "usage": usage_snapshot,
        }
        if assistant_msg_id:
            done_data["message_id"] = assistant_msg_id
        yield _make_usage_sse(conversation_id, run_id, usage_tracker)
        yield f"data: {json.dumps(done_data, ensure_ascii=False)}\n\n"

        await _update_run_status(
            run_id,
            ChatRun.StatusField.SUCCESS,
            assistant_message_id=assistant_msg_id or "",
            usage=usage_snapshot,
        )
        status_finalized = True

    except Exception as e:
        logger.exception({
            "msg": "agent_stream_error",
            "conversation_id": conversation_id,
            "run_id": run_id,
        })

        # 失败也落助手消息，便于刷新后回溯 events / 排查
        partial = "".join(assistant_content_parts).strip()
        err_brief = str(e)[:500]
        fail_content = (
            f"{partial}\n\n[执行失败] {err_brief}" if partial
            else f"[执行失败] {err_brief}"
        )
        assistant_msg_id = ""
        try:
            assistant_msg_id = await _persist_assistant_message(
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                content=fail_content,
                status=Message.StatusField.FAIL,
                skip_if_empty=False,
            ) or ""
            if assistant_msg_id:
                await ChatRunEvent.a_p_col.update_many(
                    {"run_id": run_id, "is_deleted": 0},
                    {"$set": {"parent_message_id": assistant_msg_id}},
                )
        except Exception:
            logger.exception({
                "msg": "assistant_message_save_on_error_failed",
                "conversation_id": conversation_id,
                "run_id": run_id,
            })

        usage_snapshot = usage_tracker.final_snapshot()
        await _update_run_status(
            run_id,
            ChatRun.StatusField.FAILED,
            assistant_message_id=assistant_msg_id,
            error={"code": "agent_stream_error", "message": str(e)},
            usage=usage_snapshot,
        )
        status_finalized = True
        seq = await _get_next_seq(run_id)
        error_extra: dict[str, Any] = {
            "message": "Agent stream error",
            "usage": usage_snapshot,
        }
        if assistant_msg_id:
            error_extra["message_id"] = assistant_msg_id
        yield _make_usage_sse(conversation_id, run_id, usage_tracker)
        yield _make_sse(
            "error", conversation_id, run_id, seq,
            **error_extra,
        )
    except asyncio.CancelledError:
        # 显式取消由 execute_run / logic_cancel_run 写终态并发布 cancelled；
        # 非显式取消（进程退出等）保持 running，交由幽灵清理。
        if not status_finalized and is_cancel_requested(run_id):
            try:
                usage_snapshot = usage_tracker.final_snapshot()
                await _update_run_status(
                    run_id,
                    ChatRun.StatusField.CANCELLED,
                    error={
                        "code": "user_cancelled",
                        "message": "cancelled by user",
                    },
                    usage=usage_snapshot,
                )
                seq = await _get_next_seq(run_id)
                yield _make_sse(
                    "cancelled", conversation_id, run_id, seq,
                    message="cancelled by user",
                    usage=usage_snapshot,
                )
            except Exception:
                logger.exception({
                    "msg": "chat_run_cancel_finalize_failed",
                    "run_id": run_id,
                })
        raise
    # 注意：不再在 finally 中因客户端断开将 run 标为 cancelled



async def _handle_interrupt(agent, config: dict, conversation_id: str, run_id: str) -> None:
    """处理中断：更新 ChatRun 状态

    Arguments:
        agent -- LangGraph CompiledStateGraph
        config -- Agent 配置
        conversation_id -- 会话 ID
        run_id -- 当前 run ID
    """
    now = datetime.now(timezone.utc)
    await ChatRun.a_p_col.update_one(
        {"_id": run_id},
        {"$set": {
            "status": ChatRun.StatusField.INTERRUPTED,
            "interrupted_at": now,
            "update_time": now,
        }},
    )


async def _update_run_status(
    run_id: str,
    status: str,
    assistant_message_id: str = "",
    error: dict | None = None,
    usage: dict | None = None,
) -> None:
    """更新 ChatRun 状态

    Arguments:
        run_id -- run ID（必须与 chat_run._id 一致）
        status -- 新状态
        assistant_message_id -- Assistant 消息 ID（完成时回填）
        error -- 错误信息 {"code": "...", "message": "..."}
        usage -- token 用量累计（可选）
    """
    now = datetime.now(timezone.utc)
    set_data: dict = {"status": status, "update_time": now}
    if status in (
        ChatRun.StatusField.SUCCESS,
        ChatRun.StatusField.FAILED,
        ChatRun.StatusField.CANCELLED,
    ):
        set_data["completed_at"] = now
    if assistant_message_id:
        set_data["assistant_message_id"] = assistant_message_id
    if error is not None:
        set_data["error"] = error
    if usage is not None:
        set_data["usage"] = usage

    result = await ChatRun.a_p_col.update_one({"_id": run_id}, {"$set": set_data})
    if result.matched_count == 0:
        logger.warning({
            "msg": "chat_run_status_update_missed",
            "run_id": run_id,
            "status": status,
        })
    else:
        logger.info({
            "msg": "chat_run_status_updated",
            "run_id": run_id,
            "status": status,
            "usage_total": (usage or {}).get("total_tokens"),
        })


async def _load_run_usage_tracker(run_id: str) -> RunUsageTracker:
    """从 ChatRun.usage 恢复累加器（HITL resume 时延续累计）。"""
    try:
        doc = await ChatRun.a_p_col.find_one({"_id": run_id}, {"usage": 1})
    except Exception:
        logger.exception({
            "msg": "load_run_usage_failed",
            "run_id": run_id,
        })
        return RunUsageTracker()
    usage = (doc or {}).get("usage")
    return RunUsageTracker.from_committed(usage if isinstance(usage, dict) else None)


async def _persist_run_usage(run_id: str, usage: dict[str, Any]) -> None:
    """将 token 用量写入 ChatRun（不改变 status）。"""
    if not usage:
        return
    now = datetime.now(timezone.utc)
    try:
        await ChatRun.a_p_col.update_one(
            {"_id": run_id},
            {"$set": {"usage": usage, "update_time": now}},
        )
    except Exception:
        logger.exception({
            "msg": "persist_run_usage_failed",
            "run_id": run_id,
        })


def _make_usage_sse(
    conversation_id: str,
    run_id: str,
    tracker: RunUsageTracker,
) -> str:
    """构建 usage SSE（seq=-1，不占业务序号）。"""
    return _make_sse(
        "usage",
        conversation_id,
        run_id,
        -1,
        data=tracker.snapshot(mark_emitted=True),
    )


def _extract_model_name(message: Any) -> str:
    """从消息 response_metadata 提取模型名。"""
    meta = getattr(message, "response_metadata", None) or {}
    if not isinstance(meta, dict):
        return ""
    name = meta.get("model_name") or meta.get("model") or ""
    return str(name) if name else ""


async def _persist_assistant_message(
    *,
    conversation_id: str,
    user_id: str,
    run_id: str,
    content: str,
    status: int,
    skip_if_empty: bool = True,
) -> str | None:
    """落库助手消息并更新会话摘要

    Arguments:
        conversation_id -- 会话 ID
        user_id -- 接收方用户 ID
        run_id -- 关联 run ID
        content -- 消息正文
        status -- Message.StatusField（SUCCESS / FAIL）
        skip_if_empty -- 正文为空时是否跳过（成功路径用 True）

    Returns:
        str | None -- 新消息 _id；跳过时返回 None
    """
    text = (content or "").strip()
    if skip_if_empty and not text:
        return None

    now = datetime.now(timezone.utc)
    assistant_msg = Message(
        conversation_id=conversation_id,
        sender_id="agent",
        receiver_id=user_id,
        msg_type=Message.MsgTypeField.TEXT,
        content=text or "[执行失败]",
        status=status,
        run_id=run_id,
    )
    await Message.a_p_col.insert_one(assistant_msg.to_dict())

    preview = (text or "[执行失败]")[:200]
    await Conversation.a_p_col.update_one(
        {"_id": conversation_id},
        {
            "$set": {
                "last_msg_id": assistant_msg._id,
                "last_msg_content": preview,
                "update_time": now,
            },
        },
    )

    logger.info({
        "msg": "assistant_message_saved",
        "conversation_id": conversation_id,
        "message_id": assistant_msg._id,
        "run_id": run_id,
        "status": status,
        "content_len": len(text),
    })
    return assistant_msg._id


# ---------------------------------------------------------------------------
# run_id / seq 工具函数
# ---------------------------------------------------------------------------


async def _get_next_seq(run_id: str) -> int:
    """原子分配 run 的下一个 seq（ChatRun.last_seq 自增）。

    不可从 chat_run_event 取 max：content 等流式事件不落库，
    否则会反复得到 seq=1，订阅端按 seq 去重后只剩首个 token，
    done 也被当成重复终态丢掉，前端表现为「出几个字就卡住且停不下来」。

    Arguments:
        run_id -- run ID

    Returns:
        int -- 下一个序号，从 1 开始
    """
    doc = await ChatRun.a_p_col.find_one_and_update(
        {"_id": run_id},
        {
            "$inc": {"last_seq": 1},
            "$set": {"update_time": datetime.now(timezone.utc)},
        },
        return_document=ReturnDocument.AFTER,
        projection={"last_seq": 1},
    )
    if doc is None:
        logger.warning({
            "msg": "chat_run_seq_alloc_missing",
            "run_id": run_id,
        })
        return 1
    return int(doc.get("last_seq") or 1)


# ---------------------------------------------------------------------------
# 工具输出治理
# ---------------------------------------------------------------------------


def _truncate_content(content: str, max_bytes: int = _TOOL_OUTPUT_MAX_BYTES) -> tuple[str, int, bool]:
    """截断工具输出内容

    Arguments:
        content -- 原始内容字符串
        max_bytes -- 最大字节数

    Returns:
        tuple[str, int, bool] -- (截断后内容, 原始字节数, 是否被截断)
    """
    raw_bytes = len(content.encode("utf-8"))
    if raw_bytes <= max_bytes:
        return content, raw_bytes, False

    # 逐字符截断，确保不超过 max_bytes
    truncated = content
    while len(truncated.encode("utf-8")) > max_bytes:
        truncated = truncated[:-1]
    return truncated, raw_bytes, True


def _redact_sensitive(data: dict) -> dict:
    """对敏感字段进行脱敏处理

    递归处理 dict，匹配敏感字段名（api_key, token, authorization,
    password, cookie, secret, credential），将值替换为占位符。

    Arguments:
        data -- 待处理的 dict

    Returns:
        dict -- 脱敏后的 dict
    """
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        if _SENSITIVE_KEY_PATTERN.search(key):
            result[key] = _REDACTED_VALUE
        elif isinstance(value, dict):
            result[key] = _redact_sensitive(value)
        elif isinstance(value, list):
            result[key] = [
                _redact_sensitive(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# SSE 构建 / 报告流式增量
# ---------------------------------------------------------------------------

# 报告 markdown 增量节流：累计字符或时间间隔
_REPORT_DELTA_MIN_CHARS = 80
_REPORT_DELTA_MIN_INTERVAL_S = 0.08


def _normalize_message_content(content: Any) -> str:
    """将模型 content（str / block list）规范为纯文本

    Arguments:
        content -- AIMessage(Chunk).content

    Returns:
        str -- 拼接后的文本；无法解析时返回空串
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif "text" in block:
                    parts.append(str(block.get("text") or ""))
            else:
                text = getattr(block, "text", None)
                if text:
                    parts.append(str(text))
        return "".join(parts)
    return str(content)


def _extract_reasoning_content(message: Any) -> str:
    """从 AIMessage / AIMessageChunk 提取 reasoning_content

    优先 additional_kwargs；其次 content block（type=reasoning / thinking）。

    Arguments:
        message -- AIMessage 或 AIMessageChunk

    Returns:
        str -- 思考文本；无则空串
    """
    if message is None:
        return ""
    kwargs = getattr(message, "additional_kwargs", None) or {}
    if isinstance(kwargs, dict):
        raw = kwargs.get("reasoning_content")
        if raw is None:
            raw = kwargs.get("reasoning")
        if raw is not None and str(raw):
            return str(raw)

    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype in ("reasoning", "thinking", "reasoning_content"):
                    text = (
                        block.get("reasoning")
                        or block.get("thinking")
                        or block.get("text")
                        or block.get("content")
                        or ""
                    )
                    if text:
                        parts.append(str(text))
            else:
                btype = getattr(block, "type", None)
                if btype in ("reasoning", "thinking", "reasoning_content"):
                    text = (
                        getattr(block, "reasoning", None)
                        or getattr(block, "thinking", None)
                        or getattr(block, "text", None)
                        or ""
                    )
                    if text:
                        parts.append(str(text))
        return "".join(parts)
    return ""


async def _emit_reasoning_event(
    message: Any,
    conversation_id: str,
    run_id: str,
) -> AsyncGenerator[str, None]:
    """model 节点完成后：落库并下发完整思考段

    Arguments:
        message -- 节点最后一条消息
        conversation_id -- 会话 ID
        run_id -- run ID

    Yields:
        str -- SSE 事件（无思考内容时不 yield）
    """
    if not isinstance(message, (AIMessage, AIMessageChunk)):
        return
    content = _extract_reasoning_content(message)
    if not content.strip():
        return

    seq = await _get_next_seq(run_id)
    event = ChatRunEvent(
        conversation_id=conversation_id,
        run_id=run_id,
        seq=seq,
        type=ChatRunEvent.TypeField.REASONING,
        payload={"content": content},
    )
    await ChatRunEvent.a_p_col.insert_one(event.to_dict())
    yield _make_sse(
        "reasoning", conversation_id, run_id, seq,
        data={"content": content},
    )
    logger.info({
        "msg": "reasoning_event_saved",
        "run_id": run_id,
        "seq": seq,
        "content_len": len(content),
    })


def _unescape_json_string_fragment(raw: str) -> str:
    """解码 JSON 字符串片段中的转义（支持未闭合片段）

    Arguments:
        raw -- 引号内侧的原始片段（可能尚未结束）

    Returns:
        str -- 解码后的文本；非法尾部反斜杠会被忽略
    """
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            break
        nxt = raw[i + 1]
        mapping = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        if nxt in mapping:
            out.append(mapping[nxt])
            i += 2
            continue
        if nxt == "u" and i + 5 < n:
            hex_part = raw[i + 2:i + 6]
            try:
                out.append(chr(int(hex_part, 16)))
                i += 6
                continue
            except ValueError:
                out.append(nxt)
                i += 2
                continue
        out.append(nxt)
        i += 2
    return "".join(out)


def _extract_json_string_field(buf: str, field: str) -> str | None:
    """从可能不完整的 JSON 对象文本中提取字符串字段值

    Arguments:
        buf -- 累积的 tool args JSON 文本
        field -- 字段名，如 markdown / title / topic

    Returns:
        str | None -- 已解码的（可能仍不完整的）字段值；字段尚未出现则 None
    """
    if not buf or not field:
        return None
    pattern = re.compile(
        rf'"{re.escape(field)}"\s*:\s*"',
        re.DOTALL,
    )
    match = pattern.search(buf)
    if not match:
        return None

    i = match.end()
    raw_chars: list[str] = []
    while i < len(buf):
        ch = buf[i]
        if ch == "\\":
            if i + 1 >= len(buf):
                break
            raw_chars.append(ch)
            raw_chars.append(buf[i + 1])
            i += 2
            continue
        if ch == '"':
            break
        raw_chars.append(ch)
        i += 1
    return _unescape_json_string_fragment("".join(raw_chars))


class _SubmitReportStreamTracker:
    """跟踪报告流式输出

    支持两条路径：
    1. begin_report 之后，把助手正文 content 转成 artifact_delta
       （DeepSeek 等模型往往不流式下发 tool args，此为可靠路径）
    2. submit_report 的 tool_call_chunks 参数增量（兼容能流式 args 的模型）
    """

    def __init__(self) -> None:
        # key: tool_call_id 或 index 占位
        self._by_key: dict[str, dict[str, Any]] = {}
        self._content_mode: bool = False
        self._content_state: dict[str, Any] = {
            "tool_call_id": "",
            "title": "",
            "topic": "",
            "buf": "",
            "emitted_len": 0,
            "pending_delta": "",
            "last_flush_at": 0.0,
            "started": False,
        }

    @property
    def content_mode(self) -> bool:
        """是否处于「正文即报告」流式模式"""
        return self._content_mode

    def activate_content_mode(
        self,
        *,
        tool_call_id: str,
        title: str = "",
        topic: str = "",
    ) -> None:
        """开启正文路由为报告流"""
        self._content_mode = True
        self._content_state = {
            "tool_call_id": tool_call_id or f"report_{int(time.time())}",
            "title": title or "",
            "topic": topic or "",
            "buf": "",
            "emitted_len": 0,
            "pending_delta": "",
            "last_flush_at": 0.0,
            "started": True,
        }

    def deactivate_content_mode(self) -> None:
        """关闭正文路由模式"""
        self._content_mode = False

    def pause_content_intake(self) -> None:
        """停止接收新的正文增量，但保留已累积 markdown 供 submit 回退"""
        self._content_mode = False

    def get_streamed_markdown(self) -> str:
        """获取 content 模式已累积的报告正文"""
        return str(self._content_state.get("buf") or "")

    def get_content_tool_call_id(self) -> str:
        """content 模式使用的 tool_call_id"""
        return str(self._content_state.get("tool_call_id") or "")

    async def emit_content_start(
        self,
        *,
        conversation_id: str,
        run_id: str,
    ) -> AsyncGenerator[str, None]:
        """发出 artifact_start（content 模式）"""
        state = self._content_state
        seq = await _get_next_seq(run_id)
        logger.info({
            "msg": "artifact_start_content_mode",
            "run_id": run_id,
            "tool_call_id": state["tool_call_id"],
            "title": state["title"],
        })
        yield _make_sse(
            "artifact_start",
            conversation_id,
            run_id,
            seq,
            data={
                "kind": "report",
                "tool_call_id": state["tool_call_id"],
                "title": state["title"],
                "topic": state["topic"],
                "status": "generating",
            },
        )

    async def ingest_content_delta(
        self,
        delta: str,
        *,
        conversation_id: str,
        run_id: str,
    ) -> AsyncGenerator[str, None]:
        """将助手正文增量转为 artifact_delta"""
        if not self._content_mode or not delta:
            return
        state = self._content_state
        state["buf"] += delta
        state["pending_delta"] += delta

        pending = state["pending_delta"]
        now = time.monotonic()
        should_flush = (
            len(pending) >= _REPORT_DELTA_MIN_CHARS
            or (
                state["last_flush_at"]
                and now - state["last_flush_at"] >= _REPORT_DELTA_MIN_INTERVAL_S
            )
            or (state["last_flush_at"] == 0.0 and len(pending) >= 16)
        )
        if not should_flush:
            return

        flush = pending
        state["pending_delta"] = ""
        state["last_flush_at"] = now
        state["emitted_len"] = len(state["buf"])
        seq = await _get_next_seq(run_id)
        yield _make_sse(
            "artifact_delta",
            conversation_id,
            run_id,
            seq,
            data={
                "tool_call_id": state["tool_call_id"],
                "delta": flush,
                "title": state["title"],
                "topic": state["topic"],
            },
        )

    def _state_key(self, index: Any, call_id: str) -> str:
        if call_id:
            return f"id:{call_id}"
        return f"idx:{index}"

    async def ingest(
        self,
        chunk: AIMessageChunk,
        *,
        conversation_id: str,
        run_id: str,
    ) -> AsyncGenerator[str, None]:
        """消化一条 AIMessageChunk 中的 tool_call_chunks（兼容路径）"""
        tool_call_chunks = getattr(chunk, "tool_call_chunks", None) or []
        for tc in tool_call_chunks:
            if isinstance(tc, dict):
                name = tc.get("name") or ""
                args_part = tc.get("args")
                call_id = tc.get("id") or ""
                index = tc.get("index")
            else:
                name = getattr(tc, "name", None) or ""
                args_part = getattr(tc, "args", None)
                call_id = getattr(tc, "id", None) or ""
                index = getattr(tc, "index", None)

            key = self._state_key(index, call_id)
            state = self._by_key.get(key)
            if state is None:
                state = {
                    "tool_call_id": call_id,
                    "sse_id": "",
                    "index": index,
                    "name": name,
                    "args_buf": "",
                    "started": False,
                    "emitted_markdown_len": 0,
                    "title": "",
                    "topic": "",
                    "pending_delta": "",
                    "last_flush_at": 0.0,
                }
                self._by_key[key] = state
            else:
                if call_id and not state["tool_call_id"]:
                    state["tool_call_id"] = call_id
                    new_key = self._state_key(index, call_id)
                    if new_key != key:
                        self._by_key[new_key] = state
                        del self._by_key[key]
                        key = new_key
                if name:
                    state["name"] = name

            if args_part is None:
                args_part = ""
            if isinstance(args_part, dict):
                try:
                    state["args_buf"] = json.dumps(args_part, ensure_ascii=False)
                except (TypeError, ValueError):
                    state["args_buf"] += str(args_part)
            else:
                state["args_buf"] += str(args_part)

            if state["name"] and state["name"] != SUBMIT_REPORT_TOOL:
                continue
            if not state["name"]:
                continue
            # 仅当 content 模式已流出足够正文时跳过 submit args，
            # 避免模型跳过正文、直接把全文塞进 submit 参数时前端一直「生成中」无增量
            if len(self.get_streamed_markdown()) >= 32:
                continue

            async for sse in self._emit_for_state(
                state,
                conversation_id=conversation_id,
                run_id=run_id,
                force=False,
            ):
                yield sse

    async def flush_all(
        self,
        *,
        conversation_id: str,
        run_id: str,
        force: bool = True,
    ) -> AsyncGenerator[str, None]:
        """冲刷所有报告流的 pending delta（含 content 模式）"""
        if self._content_mode:
            state = self._content_state
            pending = state.get("pending_delta") or ""
            if pending:
                state["pending_delta"] = ""
                state["last_flush_at"] = time.monotonic()
                state["emitted_len"] = len(state.get("buf") or "")
                seq = await _get_next_seq(run_id)
                yield _make_sse(
                    "artifact_delta",
                    conversation_id,
                    run_id,
                    seq,
                    data={
                        "tool_call_id": state["tool_call_id"],
                        "delta": pending,
                        "title": state["title"],
                        "topic": state["topic"],
                    },
                )

        for state in list(self._by_key.values()):
            if state.get("name") != SUBMIT_REPORT_TOOL:
                continue
            async for sse in self._emit_for_state(
                state,
                conversation_id=conversation_id,
                run_id=run_id,
                force=force,
            ):
                yield sse

    async def _emit_for_state(
        self,
        state: dict[str, Any],
        *,
        conversation_id: str,
        run_id: str,
        force: bool,
    ) -> AsyncGenerator[str, None]:
        """根据累积 args 产出 artifact_start / artifact_delta"""
        buf = state["args_buf"]
        title = _extract_json_string_field(buf, "title")
        topic = _extract_json_string_field(buf, "topic")
        markdown = _extract_json_string_field(buf, "markdown")

        if title is not None:
            state["title"] = title
        if topic is not None:
            state["topic"] = topic

        if not state["started"]:
            if not state["tool_call_id"] and (
                markdown is None or len(markdown) < 1
            ):
                return
            state["sse_id"] = (
                state["tool_call_id"]
                or f"pending_{state.get('index', 0)}"
            )
            state["started"] = True
            seq = await _get_next_seq(run_id)
            logger.info({
                "msg": "artifact_start_tool_args",
                "run_id": run_id,
                "tool_call_id": state["sse_id"],
            })
            yield _make_sse(
                "artifact_start",
                conversation_id,
                run_id,
                seq,
                data={
                    "kind": "report",
                    "tool_call_id": state["sse_id"],
                    "title": state["title"],
                    "topic": state["topic"],
                    "status": "generating",
                },
            )

        tool_call_id = state.get("sse_id") or state["tool_call_id"] or (
            f"pending_{state.get('index', 0)}"
        )

        if markdown is None:
            return

        already = state["emitted_markdown_len"]
        if len(markdown) < already:
            already = 0
            state["emitted_markdown_len"] = 0
            state["pending_delta"] = ""

        new_part = markdown[already:]
        if new_part:
            state["pending_delta"] += new_part
            state["emitted_markdown_len"] = len(markdown)

        pending = state["pending_delta"]
        if not pending:
            return

        now = time.monotonic()
        should_flush = force or (
            len(pending) >= _REPORT_DELTA_MIN_CHARS
            or (
                state["last_flush_at"]
                and now - state["last_flush_at"] >= _REPORT_DELTA_MIN_INTERVAL_S
            )
            or (state["last_flush_at"] == 0.0 and len(pending) >= 16)
        )
        if not should_flush:
            return

        delta = pending
        state["pending_delta"] = ""
        state["last_flush_at"] = now
        seq = await _get_next_seq(run_id)
        yield _make_sse(
            "artifact_delta",
            conversation_id,
            run_id,
            seq,
            data={
                "tool_call_id": tool_call_id,
                "delta": delta,
                "title": state["title"],
                "topic": state["topic"],
            },
        )


def _sse_payload(
    event_type: str,
    conversation_id: str,
    run_id: str,
    seq: int,
    **extra,
) -> dict[str, Any]:
    """构建 SSE JSON 载荷（不含 data: 前缀）。"""
    return {
        "type": event_type,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "seq": seq,
        **extra,
    }


def _format_sse_from_payload(payload: dict[str, Any]) -> str:
    """将载荷格式化为 SSE 字符串。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _make_sse(
    event_type: str,
    conversation_id: str,
    run_id: str,
    seq: int,
    **extra,
) -> str:
    """统一构建 SSE 事件字符串

    所有流式事件携带 conversation_id, run_id, seq 元信息。

    Arguments:
        event_type -- 事件类型
        conversation_id -- 会话 ID
        run_id -- run ID
        seq -- 事件序号
        **extra -- 额外的 JSON 字段（content, data, message, message_id 等）

    Returns:
        str -- "data: {...}\\n\\n" 格式的 SSE 字符串
    """
    return _format_sse_from_payload(
        _sse_payload(event_type, conversation_id, run_id, seq, **extra),
    )


def _parse_sse_payload(sse_event: str) -> dict[str, Any] | None:
    """从 SSE 字符串解析 JSON 载荷。"""
    if not sse_event or not isinstance(sse_event, str):
        return None
    line = sse_event.strip()
    if line.startswith("data:"):
        line = line[5:].strip()
    try:
        data = json.loads(line)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


async def _redis_publish_safe(run_id: str, payload: dict[str, Any]) -> None:
    """后台写 Redis，失败只记日志，不影响本地流式。"""
    try:
        await publish_run_event(run_id, payload)
    except Exception:
        logger.exception({
            "msg": "run_event_redis_publish_task_failed",
            "run_id": run_id,
            "type": payload.get("type"),
        })


async def _publish_sse_payload(payload: dict[str, Any]) -> None:
    """发布事件到本地 hub + Redis。

    last_seq 已在 ``_get_next_seq`` 中原子递增，此处不再重复写库。
    本地 hub 同步投递；Redis 异步落盘，避免 XADD 超时卡住 SSE。
    """
    if not payload:
        return
    run_id = str(payload.get("run_id") or "")
    if not run_id:
        return
    # 本地扇出优先（同进程首连不依赖 Redis）
    run_event_hub.publish(run_id, payload)
    # Redis 仅用于刷新续订，不阻塞生成与首连订阅
    asyncio.create_task(
        _redis_publish_safe(run_id, payload),
        name=f"redis-pub-{run_id}-{payload.get('seq')}",
    )


async def _publish_sse_string(run_id: str, sse_event: str) -> None:
    """解析并发布 SSE 字符串。"""
    payload = _parse_sse_payload(sse_event)
    if payload is None:
        return
    if not payload.get("run_id"):
        payload["run_id"] = run_id
    await _publish_sse_payload(payload)


async def _persist_partial_content(run_id: str, content: str) -> None:
    """节流写入流式正文快照。"""
    await ChatRun.a_p_col.update_one(
        {"_id": run_id},
        {"$set": {
            "partial_content": content,
            "update_time": datetime.now(timezone.utc),
        }},
    )


async def _persist_partial_report(run_id: str, report: dict[str, Any]) -> None:
    """节流写入报告流式草稿（刷新后恢复 ReportCard / 抽屉正文）。"""
    if not run_id or not isinstance(report, dict):
        return
    try:
        last_seq = int(report.get("last_seq") or 0)
    except (TypeError, ValueError):
        last_seq = 0
    payload = {
        "tool_call_id": str(report.get("tool_call_id") or ""),
        "title": str(report.get("title") or ""),
        "topic": str(report.get("topic") or ""),
        "markdown": str(report.get("markdown") or ""),
        "status": str(report.get("status") or "generating"),
        "last_seq": last_seq,
    }
    await ChatRun.a_p_col.update_one(
        {"_id": run_id},
        {"$set": {
            "partial_report": payload,
            "update_time": datetime.now(timezone.utc),
        }},
    )


async def _get_run_owned_by(run_id: str, user_id: str) -> dict:
    """校验 run 归属并返回文档。"""
    run_doc = await ChatRun.a_p_col.find_one({
        "_id": run_id,
        "owner_id": user_id,
        "is_deleted": 0,
    })
    if not run_doc:
        raise AppException(
            message="Run 不存在或已删除",
            code=BizCode.NOT_FOUND,
            status_code=404,
        )
    return run_doc


async def _maybe_fail_ghost_running(run_doc: dict) -> dict:
    """无本机 Task 且超时的 running → failed。"""
    if not run_doc:
        return run_doc
    run_id = run_doc.get("_id")
    if run_doc.get("status") != ChatRun.StatusField.RUNNING:
        return run_doc
    if has_active_task(run_id):
        return run_doc

    update_time = run_doc.get("update_time") or run_doc.get("started_at")
    if isinstance(update_time, datetime):
        ut = update_time if update_time.tzinfo else update_time.replace(tzinfo=timezone.utc)
    else:
        ut = datetime.now(timezone.utc) - _GHOST_RUNNING_TIMEOUT - timedelta(seconds=1)

    if datetime.now(timezone.utc) - ut < _GHOST_RUNNING_TIMEOUT:
        return run_doc

    await _update_run_status(
        run_id,
        ChatRun.StatusField.FAILED,
        error={
            "code": "ghost_running_timeout",
            "message": "run marked failed: no active worker and timed out",
        },
    )
    conversation_id = run_doc.get("conversation_id", "")
    seq = await _get_next_seq(run_id)
    await _publish_sse_payload(
        _sse_payload(
            "error", conversation_id, run_id, seq,
            message="Run timed out without active worker",
        ),
    )
    logger.warning({
        "msg": "ghost_running_failed",
        "run_id": run_id,
        "conversation_id": conversation_id,
    })
    refreshed = await ChatRun.a_p_col.find_one({"_id": run_id, "is_deleted": 0})
    return refreshed or {**run_doc, "status": ChatRun.StatusField.FAILED}


def _mongo_event_to_sse_payload(
    doc: dict,
    conversation_id: str,
) -> dict[str, Any] | None:
    """将 chat_run_event 文档转为 SSE 载荷。"""
    if not doc:
        return None
    evt_type = doc.get("type") or ""
    # SSE 对外类型与落库类型差异
    sse_type = evt_type
    if evt_type == ChatRunEvent.TypeField.TOOL_RESULT:
        sse_type = "tool_response"
    payload_data = doc.get("payload")
    extra: dict[str, Any] = {}
    if sse_type in (
        "tool_call", "tool_response", "interrupt", "approval",
        "artifact", "plan", "outline", "reasoning",
    ):
        extra["data"] = payload_data
    elif sse_type == "error":
        if isinstance(payload_data, dict):
            extra["message"] = payload_data.get("message") or "error"
        else:
            extra["message"] = str(payload_data or "error")
    else:
        extra["data"] = payload_data
    return _sse_payload(
        sse_type,
        conversation_id or doc.get("conversation_id", ""),
        doc.get("run_id", ""),
        int(doc.get("seq") or 0),
        **extra,
    )


async def _format_node_message_event(
    source: str,
    message,
    conversation_id: str,
    run_id: str,
    report_tracker: _SubmitReportStreamTracker | None = None,
) -> list[str]:
    """从节点输出消息生成 SSE 事件字符串列表并持久化

    处理 "model" 和 "tools" 节点的输出消息，提取工具调用和工具响应信息。
    - source="model" + AIMessage.tool_calls → "tool_call" 事件；
      若含 begin_report，开启正文流式并发送 artifact_start；
      若含 submit_report，额外发送终态 "artifact" 事件
    - source="tools" + ToolMessage → "tool_response" 事件

    Arguments:
        source -- 节点名称 ("model" 或 "tools")
        message -- 节点的最后一条消息
        conversation_id -- 会话 ID
        run_id -- 当前 run ID
        report_tracker -- 报告流式跟踪器（可选）

    Returns:
        list[str] -- SSE 事件字符串列表
    """
    events: list[str] = []

    if source == "model":
        # AIMessage 中包含 tool_calls → 模型决定调用工具
        if isinstance(message, AIMessage) and message.tool_calls:
            tool_calls_data = []
            for tc in message.tool_calls:
                name = tc.get("name", "")
                raw_args = tc.get("args", {}) or {}
                tool_calls_data.append({
                    "id": tc.get("id", ""),
                    "name": name,
                    "args": _sanitize_tool_args_for_trace(name, raw_args),
                })

                # 开始撰写：开启 content→artifact 路由（适配不流式 tool args 的模型）
                if name == BEGIN_REPORT_TOOL and report_tracker is not None:
                    args = raw_args if isinstance(raw_args, dict) else {}
                    report_tracker.activate_content_mode(
                        tool_call_id=tc.get("id") or f"begin_{run_id}",
                        title=str(args.get("title") or ""),
                        topic=str(args.get("topic") or ""),
                    )
                    async for sse in report_tracker.emit_content_start(
                        conversation_id=conversation_id,
                        run_id=run_id,
                    ):
                        events.append(sse)
                    # 不依赖模型再调 update_plan_step：自动推进「撰写」步骤
                    plan_sse = await _auto_plan_step_for_report(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        status="running",
                        note="开始撰写报告正文",
                    )
                    if plan_sse:
                        events.append(plan_sse)

                # 报告正文终态：独立 artifact
                if name == SUBMIT_REPORT_TOOL:
                    args = raw_args if isinstance(raw_args, dict) else {}
                    # 若 markdown 参数为空，回退到 content 模式已流式正文
                    if report_tracker is not None:
                        streamed = report_tracker.get_streamed_markdown()
                        if not str(args.get("markdown") or "").strip() and streamed:
                            args = {**args, "markdown": streamed}
                        # 优先沿用 begin 时的 tool_call_id，便于前端草稿合并
                        content_id = report_tracker.get_content_tool_call_id()
                        tool_call_id = content_id or tc.get("id", "")
                        report_tracker.deactivate_content_mode()
                    else:
                        tool_call_id = tc.get("id", "")

                    artifact_sse = await _emit_report_artifact(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        tool_call_id=tool_call_id,
                        args=args,
                    )
                    if artifact_sse:
                        events.append(artifact_sse)
                        plan_sse = await _auto_plan_step_for_report(
                            conversation_id=conversation_id,
                            run_id=run_id,
                            status="completed",
                            note="报告已提交",
                        )
                        if plan_sse:
                            events.append(plan_sse)

                # Plan 步骤进度更新
                if name == UPDATE_PLAN_STEP_TOOL:
                    args = raw_args if isinstance(raw_args, dict) else {}
                    plan_sse = await _apply_and_emit_plan_step(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        step_id=str(args.get("step_id") or ""),
                        status=str(args.get("status") or ""),
                        note=str(args.get("note") or ""),
                    )
                    if plan_sse:
                        events.append(plan_sse)

            seq = await _get_next_seq(run_id)

            # 持久化 tool_call 事件（args 已脱敏/截断）
            event = ChatRunEvent(
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq,
                type=ChatRunEvent.TypeField.TOOL_CALL,
                payload={"tool_calls": tool_calls_data},
            )
            await ChatRunEvent.a_p_col.insert_one(event.to_dict())

            events.append(_make_sse(
                "tool_call", conversation_id, run_id, seq,
                data=tool_calls_data,
            ))

        elif (
            report_tracker is not None
            and report_tracker.content_mode
            and report_tracker.get_streamed_markdown()
        ):
            # 正文撰写轮结束且本轮无工具调用：暂停路由，避免后续短句污染报告流
            report_tracker.pause_content_intake()

    elif source == "tools":
        # ToolMessage → 工具执行结果
        if isinstance(message, ToolMessage):
            raw_content = str(message.content) if message.content else ""
            preview, content_size, truncated = _truncate_content(raw_content)

            seq = await _get_next_seq(run_id)
            tool_response_data = {
                "tool_call_id": message.tool_call_id,
                "name": getattr(message, "name", ""),
                "content": preview,
                "content_size": content_size,
                "truncated": truncated,
            }

            # 持久化 tool_result 事件
            event = ChatRunEvent(
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq,
                type=ChatRunEvent.TypeField.TOOL_RESULT,
                payload={
                    "tool_call_id": message.tool_call_id,
                    "name": getattr(message, "name", ""),
                    "content_preview": preview,
                    "content_size": content_size,
                    "truncated": truncated,
                },
            )
            await ChatRunEvent.a_p_col.insert_one(event.to_dict())

            events.append(_make_sse(
                "tool_response", conversation_id, run_id, seq,
                data=tool_response_data,
            ))

    return events


def _sanitize_tool_args_for_trace(name: str, args: dict) -> dict:
    """工具参数脱敏；报告提交工具不把全文打进 trace"""
    if not isinstance(args, dict):
        return {}
    if name == SUBMIT_REPORT_TOOL:
        markdown = str(args.get("markdown") or "")
        return {
            "title": args.get("title", ""),
            "topic": args.get("topic", ""),
            "markdown_chars": len(markdown),
            "markdown_preview": (
                markdown[:120] + ("..." if len(markdown) > 120 else "")
            ),
        }
    if name == BEGIN_REPORT_TOOL:
        return {
            "title": args.get("title", ""),
            "topic": args.get("topic", ""),
        }
    if name == UPDATE_PLAN_STEP_TOOL:
        return {
            "step_id": args.get("step_id", ""),
            "status": args.get("status", ""),
            "note": str(args.get("note") or "")[:120],
        }
    return _redact_sensitive(args)


async def _emit_plan_event(
    conversation_id: str,
    run_id: str,
    plan: dict[str, Any],
) -> str:
    """落库并下发 Plan 进度快照 SSE（type=plan）"""
    seq = await _get_next_seq(run_id)
    event = ChatRunEvent(
        conversation_id=conversation_id,
        run_id=run_id,
        seq=seq,
        type=ChatRunEvent.TypeField.PLAN,
        payload=plan,
    )
    await ChatRunEvent.a_p_col.insert_one(event.to_dict())
    return _make_sse(
        "plan", conversation_id, run_id, seq,
        data=plan,
    )


def _build_approval_payload(
    *,
    hitl_response: dict[str, Any],
    interrupt_payload: dict[str, Any],
    interrupt_seq: int | None,
) -> dict[str, Any]:
    """组装可回溯的 approval 事件负载

    在用户 HITL 响应基础上附带 interrupt 上下文（reason / title / seq），
    方便历史会话还原「确认了什么」。
    """
    payload: dict[str, Any] = {
        "action": hitl_response.get("action"),
        "payload": hitl_response.get("payload"),
        "reason": str(interrupt_payload.get("reason") or ""),
        "title": str(interrupt_payload.get("title") or ""),
        "interrupt_seq": interrupt_seq,
    }
    schema = interrupt_payload.get("schema") or {}
    if isinstance(schema, dict):
        reason = payload["reason"]
        if reason == "outline_confirm" and schema.get("topic"):
            payload["topic"] = str(schema.get("topic") or "")
        if reason == "plan_confirm" and schema.get("goal"):
            payload["goal"] = str(schema.get("goal") or "")
    return payload


def _build_outline_snapshot(
    *,
    title: str = "",
    topic: str = "",
    chapters: list[dict[str, Any]] | None = None,
    action: str = "confirm",
) -> dict[str, Any]:
    """构建报告章节大纲快照（供落库与 SSE）"""
    normalized: list[dict[str, Any]] = []
    for i, chapter in enumerate(chapters or []):
        if not isinstance(chapter, dict):
            continue
        selected = bool(chapter.get("selected", True))
        normalized.append({
            "id": str(chapter.get("id") or str(i + 1)),
            "title": str(chapter.get("title") or f"章节 {i + 1}"),
            "description": str(chapter.get("description") or ""),
            "selected": selected,
        })
    selected_count = sum(1 for ch in normalized if ch["selected"])
    return {
        "title": title or "报告章节大纲",
        "topic": topic or "",
        "chapters": normalized,
        "action": action or "confirm",
        "selected_count": selected_count,
        "total_count": len(normalized),
    }


async def _emit_outline_event(
    conversation_id: str,
    run_id: str,
    outline: dict[str, Any],
) -> str:
    """落库并下发章节大纲确认快照 SSE（type=outline）"""
    seq = await _get_next_seq(run_id)
    event = ChatRunEvent(
        conversation_id=conversation_id,
        run_id=run_id,
        seq=seq,
        type=ChatRunEvent.TypeField.OUTLINE,
        payload=outline,
    )
    await ChatRunEvent.a_p_col.insert_one(event.to_dict())
    return _make_sse(
        "outline", conversation_id, run_id, seq,
        data=outline,
    )


def _find_report_write_step_id(plan: dict[str, Any] | None) -> str | None:
    """从计划中识别「撰写/提交报告」步骤 id。"""
    steps = (plan or {}).get("steps") or []
    keywords = ("撰写", "提交报告", "写报告", "报告正文", "submit", "write")
    # 优先当前 running
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("status") == "running":
            text = f"{step.get('title', '')} {step.get('description', '')}"
            if any(k in text for k in keywords):
                return str(step.get("id") or "")
    # 再找未完成且标题匹配的最后一步
    matched: str | None = None
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("status") in ("completed", "skipped"):
            continue
        text = f"{step.get('title', '')} {step.get('description', '')}"
        if any(k in text for k in keywords):
            matched = str(step.get("id") or "")
    if matched:
        return matched
    # 兜底：最后一个未完成的 selected 步骤
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        if step.get("selected", True) and step.get("status") not in (
            "completed", "skipped",
        ):
            return str(step.get("id") or "")
    return None


async def _auto_plan_step_for_report(
    conversation_id: str,
    run_id: str,
    status: str,
    note: str = "",
) -> str | None:
    """begin/submit 时自动推进撰写步骤，避免 UI 永久停在「进行中」。"""
    run_doc = await ChatRun.a_p_col.find_one({"_id": run_id, "is_deleted": 0})
    plan = (run_doc or {}).get("plan") or {}
    if not plan.get("steps"):
        return None
    step_id = _find_report_write_step_id(plan)
    if not step_id:
        return None
    # 已是目标状态则跳过，减少重复 plan 事件
    for step in plan.get("steps") or []:
        if isinstance(step, dict) and str(step.get("id")) == step_id:
            if str(step.get("status") or "") == status:
                return None
            break
    return await _apply_and_emit_plan_step(
        conversation_id=conversation_id,
        run_id=run_id,
        step_id=step_id,
        status=status,
        note=note,
    )


async def _apply_and_emit_plan_step(
    conversation_id: str,
    run_id: str,
    step_id: str,
    status: str,
    note: str = "",
) -> str | None:
    """根据 update_plan_step 更新 ChatRun.plan 并下发 plan 事件"""
    if not step_id or not status:
        logger.warning({
            "msg": "update_plan_step_missing_args",
            "run_id": run_id,
            "step_id": step_id,
            "status": status,
        })
        return None

    run_doc = await ChatRun.a_p_col.find_one({
        "_id": run_id,
        "is_deleted": 0,
    })
    current = (run_doc or {}).get("plan") or {}
    if not current.get("steps"):
        logger.warning({
            "msg": "update_plan_step_no_plan",
            "run_id": run_id,
            "step_id": step_id,
        })
        return None

    updated = apply_step_status(current, step_id, status, note=note)
    await ChatRun.a_p_col.update_one(
        {"_id": run_id},
        {"$set": {
            "plan": updated,
            "update_time": datetime.now(timezone.utc),
        }},
    )
    logger.info({
        "msg": "plan_step_updated",
        "run_id": run_id,
        "step_id": step_id,
        "status": status,
        "plan_status": updated.get("status"),
        "completed_count": updated.get("completed_count"),
        "total_count": updated.get("total_count"),
    })
    return await _emit_plan_event(
        conversation_id=conversation_id,
        run_id=run_id,
        plan=updated,
    )


async def _emit_report_artifact(
    conversation_id: str,
    run_id: str,
    tool_call_id: str,
    args: dict,
) -> str | None:
    """将 submit_report 的正文落为 artifact 事件并返回 SSE"""
    markdown = str(args.get("markdown") or "").strip()
    if not markdown:
        logger.warning({
            "msg": "submit_report_empty_markdown",
            "conversation_id": conversation_id,
            "run_id": run_id,
            "tool_call_id": tool_call_id,
        })
        return None

    title = str(args.get("title") or "").strip()
    topic = str(args.get("topic") or "").strip()
    zh_count = count_chinese_chars(markdown)
    artifact_data = {
        "kind": "report",
        "title": title or topic or "分析报告",
        "topic": topic,
        "markdown": markdown,
        "tool_call_id": tool_call_id,
        "word_count": zh_count,
    }

    seq = await _get_next_seq(run_id)
    event = ChatRunEvent(
        conversation_id=conversation_id,
        run_id=run_id,
        seq=seq,
        type=ChatRunEvent.TypeField.ARTIFACT,
        payload=artifact_data,
    )
    await ChatRunEvent.a_p_col.insert_one(event.to_dict())

    logger.info({
        "msg": "artifact_emitted",
        "conversation_id": conversation_id,
        "run_id": run_id,
        "kind": "report",
        "zh_chars": zh_count,
        "raw_chars": len(markdown),
    })

    return _make_sse(
        "artifact", conversation_id, run_id, seq,
        data=artifact_data,
    )


# ---------------------------------------------------------------------------
# 会话解析
# ---------------------------------------------------------------------------


async def _resolve_conversation(
    query: str,
    conversation_id: str,
    user_id: str,
) -> str:
    """解析会话 ID，不存在时自动创建

    Arguments:
        query -- 用户问句（用于生成默认标题）
        conversation_id -- 传入的会话 ID，可能为空
        user_id -- 用户 ID

    Returns:
        str -- 有效的会话 ID
    """
    if conversation_id:
        conv_doc = await Conversation.a_s_col.find_one({
            "_id": conversation_id,
            "owner_id": user_id,
            "is_deleted": 0,
        })
        if conv_doc is not None:
            return conversation_id

    title = query[:30] + ("..." if len(query) > 30 else "")
    new_conv = Conversation(
        title=title,
        owner_id=user_id,
    )
    await Conversation.a_p_col.insert_one(new_conv.to_dict())
    logger.info({
        "msg": "auto_create_conversation",
        "conversation_id": new_conv._id,
        "owner_id": user_id,
        "title": title,
    })
    return new_conv._id


# ---------------------------------------------------------------------------
# Agent 状态操作
# ---------------------------------------------------------------------------


def _truncate_message_content(content: str, max_chars: int = _HYDRATE_MAX_MSG_CHARS) -> str:
    """截断单条消息内容，避免超长报告占满上下文

    Arguments:
        content -- 原始文本
        max_chars -- 最大字符数

    Returns:
        str -- 截断后文本
    """
    if not content or len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n…[内容过长，已截断]"


def _trim_hydrate_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """按条数与字符预算裁剪回灌消息，优先保留最近对话

    尽量从 HumanMessage 边界起切，避免半截轮次。

    Arguments:
        messages -- 按时间正序的 LangChain 消息列表

    Returns:
        list[BaseMessage] -- 裁剪后的消息列表
    """
    if not messages:
        return []

    # 先截断单条
    normalized: list[BaseMessage] = []
    for msg in messages:
        content = _truncate_message_content(str(msg.content or ""))
        if isinstance(msg, AIMessage):
            normalized.append(AIMessage(content=content))
        else:
            normalized.append(HumanMessage(content=content))

    # 从尾部向前取，满足条数与总字符预算
    selected_rev: list[BaseMessage] = []
    total_chars = 0
    for msg in reversed(normalized):
        if len(selected_rev) >= _HYDRATE_MAX_MESSAGES:
            break
        msg_chars = len(str(msg.content or ""))
        if selected_rev and total_chars + msg_chars > _HYDRATE_MAX_TOTAL_CHARS:
            break
        selected_rev.append(msg)
        total_chars += msg_chars

    selected = list(reversed(selected_rev))

    # 对齐到第一条 HumanMessage，避免以孤立 assistant 开头
    for i, msg in enumerate(selected):
        if isinstance(msg, HumanMessage):
            if i > 0:
                selected = selected[i:]
            break

    return selected


def _count_chat_messages(messages: list) -> int:
    """统计 checkpoint 中的可见对话消息数（human/ai，不含 tool）

    Arguments:
        messages -- checkpoint messages

    Returns:
        int -- human + ai 条数
    """
    count = 0
    for msg in messages or []:
        if isinstance(msg, (HumanMessage, AIMessage)):
            # 带 tool_calls 的 AIMessage 仍算一条对话消息
            count += 1
            continue
        msg_type = getattr(msg, "type", None)
        if msg_type in ("human", "ai"):
            count += 1
    return count


async def _load_db_chat_history(
    conversation_id: str,
    exclude_message_id: str = "",
) -> list[BaseMessage]:
    """从 message 表加载会话历史并转为 LangChain 消息

    Arguments:
        conversation_id -- 会话 ID
        exclude_message_id -- 排除的消息 ID（通常为本轮刚写入的用户消息）

    Returns:
        list[BaseMessage] -- 时间正序的 HumanMessage / AIMessage
    """
    cursor = (
        Message.a_p_col.find({
            "conversation_id": conversation_id,
            "is_deleted": 0,
            "status": Message.StatusField.SUCCESS,
        })
        .sort("create_time", 1)
    )
    docs = await cursor.to_list(length=500)

    history: list[BaseMessage] = []
    for doc in docs:
        if exclude_message_id and doc.get("_id") == exclude_message_id:
            continue
        content = (doc.get("content") or "").strip()
        if not content:
            continue
        if doc.get("sender_id") == "agent":
            history.append(AIMessage(content=content))
        else:
            history.append(HumanMessage(content=content))
    return history


async def _hydrate_thread_from_messages(
    agent,
    config: dict,
    conversation_id: str,
    exclude_message_id: str = "",
) -> None:
    """当 checkpoint 缺失或明显落后于业务消息表时，从 message 回灌

    业务 message 表是 UI/审计真相来源；LangGraph checkpoint 是 Agent 短期记忆。
    历史会话在启用 Mongo checkpointer 前可能只有 message、没有 checkpoint，
    续聊前需按限额回灌，避免模型「失忆」或上下文过长。

    Arguments:
        agent -- LangGraph CompiledStateGraph
        config -- 含 thread_id 的 Agent 配置
        conversation_id -- 会话 ID
        exclude_message_id -- 本轮用户消息 ID（由 astream 输入追加，勿重复写入）
    """
    try:
        state = await agent.aget_state(config)
        existing = []
        if state and state.values:
            existing = list(state.values.get("messages") or [])

        db_history = await _load_db_chat_history(
            conversation_id=conversation_id,
            exclude_message_id=exclude_message_id,
        )
        if not db_history:
            return

        cp_count = _count_chat_messages(existing)
        # checkpoint 已覆盖「可回灌窗口」则跳过，避免长会话每轮重复清空重写
        # 例：库中 100 条、窗口 20 → 回灌后 cp>=20 即视为已跟上
        if cp_count >= min(len(db_history), _HYDRATE_MAX_MESSAGES):
            return

        trimmed = _trim_hydrate_messages(db_history)
        if not trimmed:
            return

        # 先清空再写入，避免与残缺 checkpoint 叠加重复
        await agent.aupdate_state(
            config,
            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *trimmed]},
        )
        logger.info({
            "msg": "thread_hydrated_from_message_db",
            "conversation_id": conversation_id,
            "db_history_count": len(db_history),
            "checkpoint_chat_count": cp_count,
            "hydrated_count": len(trimmed),
            "hydrated_chars": sum(len(str(m.content or "")) for m in trimmed),
        })
    except Exception:
        logger.exception({
            "msg": "thread_hydrate_failed",
            "conversation_id": conversation_id,
        })


async def _auto_clear_stale_interrupt(
    agent,
    config: dict,
    conversation_id: str = "",
) -> None:
    """自动清理残留的中断状态

    如果会话上次被中断但未恢复（用户关闭浏览器等），
    自动清除待执行的工具调用，让新的消息可以正常进入，
    并将仍处于 interrupted 的 ChatRun 标记为 cancelled。

    Arguments:
        agent -- LangGraph CompiledStateGraph
        config -- Agent 配置
        conversation_id -- 会话 ID（用于回填 ChatRun 状态）
    """
    # 先收尾遗留 interrupted run，避免永久停留在 interrupted
    if conversation_id:
        try:
            now = datetime.now(timezone.utc)
            result = await ChatRun.a_p_col.update_many(
                {
                    "conversation_id": conversation_id,
                    "status": ChatRun.StatusField.INTERRUPTED,
                    "is_deleted": 0,
                },
                {"$set": {
                    "status": ChatRun.StatusField.CANCELLED,
                    "completed_at": now,
                    "update_time": now,
                    "error": {
                        "code": "stale_interrupt_cleared",
                        "message": "cleared because a new message started",
                    },
                }},
            )
            if result.modified_count:
                logger.info({
                    "msg": "stale_interrupted_runs_cancelled",
                    "conversation_id": conversation_id,
                    "count": result.modified_count,
                })
        except Exception:
            logger.exception({
                "msg": "stale_interrupted_runs_cancel_error",
                "conversation_id": conversation_id,
            })

    try:
        state = await agent.aget_state(config)
        if not state or not state.values:
            return

        # 检查是否有未决中断
        if not state.interrupts:
            return

        # 清除所有待执行的 tool_calls
        messages: list = list(state.values.get("messages", []))
        modified = False
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                if isinstance(msg, AIMessage):
                    messages[i] = msg.model_copy(update={"tool_calls": []})
                    modified = True
                break

        if modified:
            await agent.aupdate_state(config, {"messages": messages})
            logger.info({
                "msg": "auto_cleared_stale_interrupt",
                "conversation_id": conversation_id or config.get("configurable", {}).get("thread_id"),
            })
    except Exception:
        logger.exception({
            "msg": "auto_clear_interrupt_error",
            "conversation_id": conversation_id or config.get("configurable", {}).get("thread_id"),
        })


async def _extract_interrupt_payload(agent, config: dict) -> dict:
    """从 Agent 状态提取统一的 HITL interrupt payload

    优先读取 LangGraph ``state.interrupts`` 中的结构化值
    （例如 request_user_confirmation 传入的 outline_confirm）。
    若无，则回退为待执行 tool_calls 的 tool_approval 形态。

    Returns:
        dict -- {reason, title?, schema?, actions?, tool_calls}
    """
    try:
        state = await agent.aget_state(config)
        if state and getattr(state, "interrupts", None):
            for item in state.interrupts:
                value = getattr(item, "value", None)
                if value is None and isinstance(item, tuple) and item:
                    value = item[0]
                if isinstance(value, dict) and value.get("reason"):
                    payload = dict(value)
                    payload.setdefault("tool_calls", [])
                    return payload
                if value is not None and not isinstance(value, dict):
                    return {
                        "reason": "user_input",
                        "title": "需要您的确认",
                        "schema": {"type": "text", "message": str(value)},
                        "actions": [
                            {"id": "confirm", "label": "确认"},
                            {"id": "cancel", "label": "取消"},
                        ],
                        "tool_calls": [],
                    }

        tool_calls_info = await _extract_pending_tool_calls(agent, config)
        if tool_calls_info:
            return {
                "reason": "tool_approval",
                "title": "工具调用确认",
                "schema": {"type": "tool_approval"},
                "actions": [
                    {"id": "approve", "label": "批准"},
                    {"id": "deny", "label": "拒绝"},
                ],
                "tool_calls": tool_calls_info,
            }
    except Exception:
        logger.exception({
            "msg": "extract_interrupt_payload_error",
            "conversation_id": config.get("configurable", {}).get("thread_id"),
        })

    return {
        "reason": "unknown",
        "title": "需要您的确认",
        "schema": {"type": "unknown"},
        "actions": [],
        "tool_calls": [],
    }


async def _extract_pending_tool_calls(agent, config: dict) -> list[dict]:
    """从 Agent 状态中提取待执行的工具调用

    在 interrupt_before=["tools"] 触发后调用，
    从 State 的最后一条 AI 消息中提取 tool_calls。

    Arguments:
        agent -- LangGraph CompiledStateGraph
        config -- Agent 配置

    Returns:
        list[dict] -- 工具调用列表 [{id, name, args}, ...]
    """
    try:
        state = await agent.aget_state(config)
        if not state or not state.values:
            return []

        messages: list = state.values.get("messages", [])
        # 倒序查找最后一条 AI 消息中的 tool_calls
        for msg in reversed(messages):
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                return [
                    {
                        "id": tc.get("id", ""),
                        "name": tc.get("name", ""),
                        "args": _redact_sensitive(tc.get("args", {})),
                    }
                    for tc in tool_calls
                ]
        return []
    except Exception:
        logger.exception({
            "msg": "extract_tool_calls_error",
            "conversation_id": config.get("configurable", {}).get("thread_id"),
        })
        return []


async def _clear_pending_tool_calls(agent, config: dict) -> None:
    """清除 State 中最后一条 AI 消息的 tool_calls（拒绝工具调用时使用）

    修改 State 使得恢复后 tools 节点跳过执行。

    Arguments:
        agent -- LangGraph CompiledStateGraph
        config -- Agent 配置
    """
    try:
        state = await agent.aget_state(config)
        if not state or not state.values:
            return

        messages: list = list(state.values.get("messages", []))
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                # 复制消息并清除 tool_calls
                if isinstance(msg, AIMessage):
                    cleared_msg = msg.model_copy(update={"tool_calls": []})
                    messages[i] = cleared_msg
                    await agent.aupdate_state(config, {"messages": messages})
                    logger.info({
                        "msg": "cleared_pending_tool_calls",
                        "conversation_id": config.get("configurable", {}).get("thread_id"),
                    })
                return
    except Exception:
        logger.exception({
            "msg": "clear_tool_calls_error",
            "conversation_id": config.get("configurable", {}).get("thread_id"),
        })
