"""
    middleware.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    通用中间件

    :author: lcg
    :date created: 2026/8/1

"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class TraceIDMiddleware(BaseHTTPMiddleware):
    """确保每个请求都携带 ``x-trace-id``。

    若客户端传入则透传，否则自动生成 UUIDv4。
    ID 写入 ``request.state.trace_id`` 并在响应头中回传。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get("x-trace-id")
        if not trace_id:
            trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id

        response = await call_next(request)
        response.headers["x-trace-id"] = trace_id
        return response
