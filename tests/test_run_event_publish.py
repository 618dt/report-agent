"""seq=-1 不写 Redis、本地 hub 仍扇出（不连真实 Redis）。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.logic import run_event_hub
from app.utils.run_event_bus import (
    event_seq,
    publish_run_event,
    should_persist_to_stream,
)


def test_event_seq_and_persist_filter():
    assert event_seq({"seq": -1}) == -1
    assert event_seq({"seq": 0}) == 0
    assert event_seq({"seq": 7}) == 7
    assert event_seq({"seq": "3"}) == 3
    assert event_seq({}) == -1
    assert event_seq({"seq": "x"}) == -1
    assert should_persist_to_stream({"seq": -1, "type": "reasoning"}) is False
    assert should_persist_to_stream({"seq": -1, "type": "usage"}) is False
    assert should_persist_to_stream({"seq": 1, "type": "content"}) is True
    assert should_persist_to_stream({"seq": 0, "type": "run_started"}) is True


def test_publish_run_event_skips_negative_seq():
    async def _run():
        mock_client = MagicMock()
        mock_client.xadd = AsyncMock()
        with patch("app.utils.run_event_bus.get_async_client", return_value=mock_client):
            result = await publish_run_event(
                "run-1",
                {"type": "reasoning", "run_id": "run-1", "seq": -1, "data": {"delta": "想"}},
            )
            assert result is None
            mock_client.xadd.assert_not_called()

    asyncio.run(_run())


def test_publish_run_event_writes_nonneg_seq():
    async def _run():
        mock_client = MagicMock()
        mock_client.xadd = AsyncMock(return_value="1-0")
        with patch("app.utils.run_event_bus.get_async_client", return_value=mock_client):
            result = await publish_run_event(
                "run-1",
                {"type": "content", "run_id": "run-1", "seq": 2, "content": "hi"},
            )
            assert result == "1-0"
            mock_client.xadd.assert_awaited_once()

    asyncio.run(_run())


def test_publish_sse_payload_hub_only_for_seq_negative():
    async def _run():
        from app.logic.chat import _publish_sse_payload

        run_id = "run-hub-skip-redis"
        queue = run_event_hub.subscribe(run_id)
        try:
            with patch("app.logic.chat.publish_run_event", new_callable=AsyncMock) as mock_pub:
                await _publish_sse_payload({
                    "type": "reasoning",
                    "run_id": run_id,
                    "seq": -1,
                    "data": {"delta": "思考"},
                })
                await asyncio.sleep(0)
                mock_pub.assert_not_called()
            got = queue.get_nowait()
            assert got["type"] == "reasoning"
            assert got["seq"] == -1
        finally:
            run_event_hub.unsubscribe(run_id, queue)

    asyncio.run(_run())


def test_publish_sse_payload_writes_redis_for_content():
    async def _run():
        from app.logic.chat import _publish_sse_payload

        run_id = "run-hub-write-redis"
        queue = run_event_hub.subscribe(run_id)
        try:
            with patch("app.logic.chat.publish_run_event", new_callable=AsyncMock) as mock_pub:
                await _publish_sse_payload({
                    "type": "content",
                    "run_id": run_id,
                    "seq": 4,
                    "content": "正文",
                })
                await asyncio.sleep(0.05)
                mock_pub.assert_awaited_once()
            got = queue.get_nowait()
            assert got["seq"] == 4
            assert got["content"] == "正文"
        finally:
            run_event_hub.unsubscribe(run_id, queue)

    asyncio.run(_run())
