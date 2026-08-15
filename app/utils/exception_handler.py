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
