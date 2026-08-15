"""
    langfuse_tracing.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    Langfuse 追踪初始化与 LangChain CallbackHandler 接入。

    优先读取 cluster.configs.yaml 的 langfuse 段，其次回退到环境变量
    LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL。

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from app.configs import cluster_configs, node_configs
from app.utils.log import logger

_initialized: bool = False
_enabled: bool = False

# 敏感字段模式（与 chat 工具输出脱敏保持一致）
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|token|authorization|password|cookie|secret|credential)",
    re.IGNORECASE,
)
_REDACTED = "***REDACTED***"


def _mask_data(data: Any, **_kwargs: Any) -> Any:
    """递归脱敏敏感键值，避免密钥进入 Langfuse。"""
    if isinstance(data, dict):
        masked: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(key, str) and _SENSITIVE_KEY_PATTERN.search(key):
                masked[key] = _REDACTED
            else:
                masked[key] = _mask_data(value)
        return masked
    if isinstance(data, list):
        return [_mask_data(item) for item in data]
    if isinstance(data, tuple):
        return tuple(_mask_data(item) for item in data)
    return data


def _resolve_langfuse_settings() -> dict[str, Any]:
    """合并 YAML 配置与环境变量，返回 Langfuse 连接设置。

    Returns:
        dict -- 含 enabled/public_key/secret_key/base_url/environment/sample_rate
    """
    cfg = cluster_configs.get("langfuse") or {}
    if not isinstance(cfg, dict):
        cfg = {}

    public_key = (
        cfg.get("public_key")
        or os.getenv("LANGFUSE_PUBLIC_KEY")
        or ""
    ).strip()
    secret_key = (
        cfg.get("secret_key")
        or os.getenv("LANGFUSE_SECRET_KEY")
        or ""
    ).strip()
    base_url = (
        cfg.get("base_url")
        or os.getenv("LANGFUSE_BASE_URL")
        or os.getenv("LANGFUSE_HOST")
        or "https://cloud.langfuse.com"
    ).strip()

    environment = (
        cfg.get("environment")
        or os.getenv("LANGFUSE_TRACING_ENVIRONMENT")
        or node_configs.get("server", {}).get("debug")
    )
    if environment is True:
        environment = "development"
    elif environment is False:
        environment = "production"
    elif not environment:
        environment = "development"
    else:
        environment = str(environment)

    sample_rate = cfg.get("sample_rate", 1.0)
    try:
        sample_rate = float(sample_rate)
    except (TypeError, ValueError):
        sample_rate = 1.0

    enabled_flag = cfg.get("enabled")
    if enabled_flag is None:
        enabled = bool(public_key and secret_key)
    else:
        enabled = bool(enabled_flag) and bool(public_key and secret_key)

    return {
        "enabled": enabled,
        "public_key": public_key,
        "secret_key": secret_key,
        "base_url": base_url,
        "environment": environment,
        "sample_rate": sample_rate,
        "release": str(cfg.get("release") or "0.1.0"),
    }


def init_langfuse() -> bool:
    """初始化 Langfuse 客户端单例。

    无密钥或 enabled=false 时跳过，不影响业务启动。

    Returns:
        bool -- 是否成功启用追踪
    """
    global _initialized, _enabled
    if _initialized:
        return _enabled

    settings = _resolve_langfuse_settings()
    if not settings["enabled"]:
        _initialized = True
        _enabled = False
        logger.info({
            "msg": "langfuse_disabled",
            "reason": "missing_keys_or_disabled",
        })
        return False

    try:
        from langfuse import Langfuse

        langfuse = Langfuse(
            public_key=settings["public_key"],
            secret_key=settings["secret_key"],
            base_url=settings["base_url"],
            environment=settings["environment"],
            release=settings["release"],
            sample_rate=settings["sample_rate"],
            mask=_mask_data,
            tracing_enabled=True,
        )
        ok = False
        try:
            ok = bool(langfuse.auth_check())
        except Exception as exc:  # noqa: BLE001 — 认证失败不应阻断启动
            logger.warning({
                "msg": "langfuse_auth_check_failed",
                "error": str(exc),
            })

        _initialized = True
        _enabled = True
        logger.info({
            "msg": "langfuse_initialized",
            "base_url": settings["base_url"],
            "environment": settings["environment"],
            "sample_rate": settings["sample_rate"],
            "auth_ok": ok,
        })
        return True
    except Exception:
        _initialized = True
        _enabled = False
        logger.exception({"msg": "langfuse_init_failed"})
        return False


def is_langfuse_enabled() -> bool:
    """返回 Langfuse 是否已启用。"""
    if not _initialized:
        init_langfuse()
    return _enabled


def flush_langfuse() -> None:
    """刷新未发送的追踪事件（短生命周期脚本或进程退出前调用）。"""
    if not _enabled:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:
        logger.exception({"msg": "langfuse_flush_failed"})


def shutdown_langfuse() -> None:
    """关闭 Langfuse 客户端并刷新缓冲。"""
    if not _enabled:
        return
    try:
        from langfuse import get_client

        get_client().shutdown()
    except Exception:
        logger.exception({"msg": "langfuse_shutdown_failed"})


def attach_langfuse_callbacks(
    config: dict,
    *,
    trace_name: str,
    user_id: str = "",
    session_id: str = "",
    tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """向 LangGraph/LangChain config 注入 Langfuse CallbackHandler 与追踪属性。

    每个请求新建 CallbackHandler，避免并发下共享 handler 的 last_trace_id 串扰。

    Arguments:
        config {dict} -- 原有 runnable config（含 configurable 等）
        trace_name {str} -- 根 run 名称（如 chat-response / chat-resume）
        user_id {str} -- 用户 ID
        session_id {str} -- 会话 ID（对应 Langfuse session）
        tags {list[str] | None} -- 业务标签
        metadata {dict | None} -- 额外元数据（run_id 等）

    Returns:
        dict -- 合并 callbacks / metadata / run_name 后的新 config
    """
    if not is_langfuse_enabled():
        return config

    try:
        from langfuse.langchain import CallbackHandler
    except Exception:
        logger.exception({"msg": "langfuse_callback_import_failed"})
        return config

    merged = dict(config or {})
    callbacks = list(merged.get("callbacks") or [])
    callbacks.append(CallbackHandler())
    merged["callbacks"] = callbacks
    merged["run_name"] = trace_name

    existing_meta = dict(merged.get("metadata") or {})
    if user_id:
        existing_meta["langfuse_user_id"] = user_id
    if session_id:
        existing_meta["langfuse_session_id"] = session_id
    if tags:
        existing_meta["langfuse_tags"] = list(tags)
    if metadata:
        for key, value in metadata.items():
            if value is None:
                continue
            # 避免覆盖 langfuse_* 保留键
            if key.startswith("langfuse_"):
                continue
            existing_meta[key] = value
    merged["metadata"] = existing_meta
    return merged
