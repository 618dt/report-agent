#!/usr/bin/env python
"""
测试 Redis Stream 是否满足对话可恢复流（标准版）所需能力。

覆盖与业务一致的命令：
  PING / INFO / XADD / XLEN / XRANGE / XREVRANGE / XREAD / EXPIRE / DEL
业务 key 格式：
  chat:run:{run_id}:stream

用法（在项目根目录，使用你跑后端的同一个 Python 环境）:
    python scripts/test_redis_stream.py

配置读取顺序：
  1) app/configs/cluster.configs.yaml 的 redis 段（与后端一致）
  2) 环境变量 REDIS_HOST / REDIS_PORT / REDIS_DB / REDIS_PASSWORD
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load_redis_config() -> dict:
    """从 cluster.configs.yaml 或环境变量加载 Redis 配置。"""
    cfg_path = _ROOT / "app" / "configs" / "cluster.configs.yaml"
    if cfg_path.is_file():
        try:
            import yaml  # type: ignore
        except ImportError:
            yaml = None
        if yaml is not None:
            with cfg_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            redis_cfg = data.get("redis") or {}
            if redis_cfg:
                return redis_cfg

    return {
        "host": os.getenv("REDIS_HOST", "localhost"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
        "db": int(os.getenv("REDIS_DB", "0")),
        "is_auth": bool(os.getenv("REDIS_PASSWORD")),
        "password": os.getenv("REDIS_PASSWORD") or "",
        "socket_timeout": 5,
        "socket_connect_timeout": 5,
    }


def _build_clients(redis_cfg: dict):
    try:
        from redis import Redis
        from redis.asyncio import Redis as AsyncRedis
    except ImportError as e:
        raise SystemExit(
            f"缺少 redis 包，请在后端同一环境安装: pip install redis\n{e}"
        ) from e

    is_auth = bool(redis_cfg.get("is_auth", False))
    password = redis_cfg.get("password") if is_auth else None
    if password == "":
        password = None

    common = dict(
        host=redis_cfg.get("host", "localhost"),
        port=int(redis_cfg.get("port", 6379)),
        db=int(redis_cfg.get("db", 0)),
        password=password,
        socket_timeout=float(redis_cfg.get("socket_timeout", 5)),
        socket_connect_timeout=float(redis_cfg.get("socket_connect_timeout", 5)),
        decode_responses=True,
    )
    sync_client = Redis(**common)
    async_client = AsyncRedis(**common)
    return sync_client, async_client, common


def _ok(name: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"[PASS] {name}{suffix}")


def _fail(name: str, err: Exception | str) -> None:
    print(f"[FAIL] {name} — {err}")


def _parse_version(info: dict) -> tuple[int, int, int]:
    raw = str(info.get("redis_version") or "0.0.0")
    parts = raw.split(".")
    nums: list[int] = []
    for p in parts[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        nums.append(int(digits or "0"))
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def stream_key(run_id: str) -> str:
    return f"chat:run:{run_id}:stream"


async def _run(keep_key: bool = False) -> int:
    redis_cfg = _load_redis_config()
    sync_client, async_client, common = _build_clients(redis_cfg)

    failures = 0
    run_id = f"stream-test-{uuid.uuid4().hex[:12]}"
    key = stream_key(run_id)

    print("=" * 60)
    print("Redis Stream 自检（对话可恢复流依赖）")
    print(
        f"connect = {common['host']}:{common['port']}"
        f" db={common['db']} auth={'yes' if common['password'] else 'no'}"
    )
    print(f"test key = {key}")
    print("=" * 60)

    # PING
    try:
        if sync_client.ping() is not True:
            raise RuntimeError("sync ping returned falsy")
        _ok("sync PING")
    except Exception as e:
        _fail("sync PING", e)
        failures += 1

    try:
        if await async_client.ping() is not True:
            raise RuntimeError("async ping returned falsy")
        _ok("async PING")
    except Exception as e:
        _fail("async PING", e)
        failures += 1

    # INFO / version
    try:
        info = sync_client.info("server")
        ver = info.get("redis_version", "?")
        mode = info.get("redis_mode", "?")
        version_tuple = _parse_version(info)
        _ok("INFO server", f"redis_version={ver}, mode={mode}")
        if version_tuple < (5, 0, 0):
            _fail(
                "version check",
                f"当前 {ver} < 5.0，不支持 Streams（会 unknown command 'XADD'）",
            )
            failures += 1
        else:
            _ok("version check", ">= 5.0，支持 Streams")
    except Exception as e:
        _fail("INFO server", e)
        failures += 1

    print("-" * 60)

    # sync XADD
    try:
        entry_id = sync_client.xadd(
            key,
            {"payload": json.dumps({"type": "run_started", "seq": 0}, ensure_ascii=False)},
            maxlen=1000,
            approximate=True,
        )
        _ok("sync XADD", f"id={entry_id}")
    except Exception as e:
        _fail("sync XADD", e)
        failures += 1
        print(
            "\n>>> XADD 失败常见原因：Redis < 5.0，或连到了旧实例（如 3.2.100）。\n"
            ">>> Windows 建议：Docker `redis:7`，或社区 Win 构建 >= 5.0.14 / 7.x\n"
        )

    try:
        n = int(sync_client.xlen(key))
        _ok("sync XLEN", f"len={n}")
        if n < 1:
            _fail("sync XLEN expect >= 1", f"got {n}")
            failures += 1
    except Exception as e:
        _fail("sync XLEN", e)
        failures += 1

    try:
        rows = sync_client.xrange(key, min="-", max="+", count=10)
        _ok("sync XRANGE", f"entries={len(rows or [])}")
    except Exception as e:
        _fail("sync XRANGE", e)
        failures += 1

    try:
        tail = sync_client.xrevrange(key, count=1)
        _ok("sync XREVRANGE", f"last={tail[0][0] if tail else None}")
    except Exception as e:
        _fail("sync XREVRANGE", e)
        failures += 1

    # async path（与后端 get_async_client 同类用法）
    try:
        entry_id2 = await async_client.xadd(
            key,
            {"payload": json.dumps(
                {"type": "content", "seq": 1, "content": "你好"},
                ensure_ascii=False,
            )},
            maxlen=1000,
            approximate=True,
        )
        _ok("async XADD", f"id={entry_id2}")
    except Exception as e:
        _fail("async XADD", e)
        failures += 1

    try:
        rows = await async_client.xrange(key, min="-", max="+")
        _ok("async XRANGE", f"entries={len(rows or [])}")
    except Exception as e:
        _fail("async XRANGE", e)
        failures += 1

    try:
        last = await async_client.xrevrange(key, count=1)
        cursor = str(last[0][0]) if last else "0-0"
        await async_client.xadd(
            key,
            {"payload": json.dumps(
                {"type": "content", "seq": 2, "content": "世界"},
                ensure_ascii=False,
            )},
        )
        resp = await async_client.xread({key: cursor}, block=1000, count=10)
        got = 0
        if resp:
            for _name, entries in resp:
                got += len(entries or [])
        if got < 1:
            raise RuntimeError(f"XREAD 未读到新消息（cursor={cursor}）")
        _ok("async XREAD", f"new_entries={got}, after={cursor}")
    except Exception as e:
        _fail("async XREAD", e)
        failures += 1

    try:
        await async_client.expire(key, 60)
        ttl = await async_client.ttl(key)
        _ok("async EXPIRE/TTL", f"ttl={ttl}s")
    except Exception as e:
        _fail("async EXPIRE/TTL", e)
        failures += 1

    # 模拟业务：连续写入多条 content，再回放
    print("-" * 60)
    print("模拟业务扇出（连续 content + 回放）")
    bus_run_id = f"bus-test-{uuid.uuid4().hex[:12]}"
    bus_key = stream_key(bus_run_id)
    try:
        for seq, text in enumerate(["run_started", "我", "正在", "回答"], start=0):
            payload = {
                "type": "run_started" if seq == 0 else "content",
                "run_id": bus_run_id,
                "seq": seq,
            }
            if seq > 0:
                payload["content"] = text
            await async_client.xadd(
                bus_key,
                {"payload": json.dumps(payload, ensure_ascii=False)},
                maxlen=10000,
                approximate=True,
            )
        rows = await async_client.xrange(bus_key, min="-", max="+")
        contents = []
        for _eid, fields in rows or []:
            raw = fields.get("payload")
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and data.get("type") == "content":
                contents.append(data.get("content", ""))
        joined = "".join(contents)
        if joined != "我正在回答":
            raise RuntimeError(f"回放正文不正确: {joined!r}")
        _ok("business-like replay", f"text={joined!r}, entries={len(rows or [])}")
    except Exception as e:
        _fail("business-like replay", e)
        failures += 1

    # 清理
    print("-" * 60)
    if keep_key:
        print(f"[INFO] --keep：保留 key 供 GUI 查看: {key}, {bus_key}")
    else:
        try:
            await async_client.delete(key, bus_key)
            _ok("cleanup DEL")
        except Exception as e:
            _fail("cleanup DEL", e)
            failures += 1

    try:
        await async_client.aclose()
    except Exception:
        pass
    try:
        sync_client.close()
    except Exception:
        pass

    print("=" * 60)
    if failures:
        print(f"结果: FAILED（{failures} 项）")
        print("建议:")
        print("  1) INFO 中 redis_version 必须 >= 5.0（你之前是 3.2.100）")
        print("  2) 确认本脚本连接的 host/port/db 与 ARDM 一致")
        print("  3) 停掉旧 redis-server 后再启新版本，避免仍占 6379")
        return 1

    print("结果: ALL PASSED — Redis Stream 可用于对话扇出")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="测试 Redis Stream（对话扇出依赖）")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="测试后不删除 key，方便在 Redis GUI 里查看",
    )
    args = parser.parse_args()
    try:
        code = asyncio.run(_run(keep_key=args.keep))
    except KeyboardInterrupt:
        print("\n中断")
        code = 130
    except Exception as e:
        print(f"[FAIL] unexpected — {e}")
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
