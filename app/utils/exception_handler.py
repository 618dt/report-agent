"""
    exception_handler.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.log import logger
from app.utils.response import error


@dataclass(frozen=True)
class AppException(Exception):
    """
    Business exception for APIs.

    - `status_code` controls the HTTP status code.
    - `code` is an application-level error code.
    - `message` is a user-facing error message.
    - `detail` can include extra debugging context (kept in response for now).
    """

    message: str
    code: int = 50000
    status_code: int = 400
    detail: Optional[Any] = None


class GlobalExceptionHandler:
    @staticmethod
    def register(app: FastAPI) -> None:
        @app.exception_handler(AppException)
        async def _handle_app_exception(request: Request, exc: AppException):
            return JSONResponse(
                status_code=exc.status_code,
                content=error(
                    code=exc.code,
                    message=exc.message,
                    detail=exc.detail,
                    request=request,
                ),
            )

        @app.exception_handler(RequestValidationError)
        async def _handle_validation_error(request: Request, exc: RequestValidationError):
            return JSONResponse(
                status_code=422,
                content=error(
                    code=42200,
                    message="Validation error",
                    detail=exc.errors(),
                    request=request,
                ),
            )

        @app.exception_handler(StarletteHTTPException)
        async def _handle_http_exception(request: Request, exc: StarletteHTTPException):
            return JSONResponse(
                status_code=exc.status_code,
                content=error(
                    code=exc.status_code * 100,
                    message=str(exc.detail),
                    request=request,
                ),
            )

        @app.exception_handler(Exception)
        async def _handle_unhandled_exception(request: Request, exc: Exception):
            logger.exception("Unhandled exception: %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=500,
                content=error(
                    code=50000,
                    message="Internal server error",
                    request=request,
                ),
            )


def friendly_agent_error(exc: BaseException) -> tuple[str, str]:
    """把模型/流式异常转成用户可读文案，原始错误只留在日志与 ChatRun.error。

    Returns:
        tuple[str, str] -- (error_code, user_message)
    """
    text = str(exc or "").lower()
    status = _http_status_from_exc(exc)

    if (
        status == 402
        or "insufficient balance" in text
        or "insufficient_quota" in text
        or "exceeded your current quota" in text
        or "billing_not_active" in text
    ):
        return "llm_insufficient_balance", "模型服务余额不足，请充值后再试。"

    if status == 429 or "rate limit" in text or "too many requests" in text:
        return "llm_rate_limited", "模型服务请求过于频繁，请稍后再试。"

    if status in (401, 403) or "invalid api key" in text or "authentication" in text:
        return "llm_auth_failed", "模型服务鉴权失败，请检查接口配置。"

    if "timeout" in text or "timed out" in text:
        return "llm_timeout", "模型服务响应超时，请稍后重试。"

    if "context length" in text or "maximum context" in text or "too many tokens" in text:
        return "llm_context_overflow", "对话上下文过长，请新开会话后再试。"

    if status and 500 <= status < 600:
        return "llm_unavailable", "模型服务暂时不可用，请稍后重试。"

    return "agent_stream_error", "服务暂时遇到问题，请稍后重试。"


def _http_status_from_exc(exc: BaseException) -> int | None:
    for attr in ("status_code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 400 <= value < 600:
            return value
    text = str(exc or "").lower()
    if "error code: 402" in text:
        return 402
    if "error code: 429" in text:
        return 429
    if "error code: 401" in text:
        return 401
    return None
