"""
    response.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    API 响应辅助函数

    :author: lcg
    :date created: 2026/8/1

"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Request

from app.constants.business_codes import BizCode
from app.models.response import ApiResponse


def _resolve_trace_id(
    request: Optional[Request] = None,
    trace_id: Optional[str] = None,
) -> Optional[str]:
    if trace_id:
        return trace_id
    if request is not None:
        return getattr(request.state, "trace_id", None)
    return None


def success(
    *,
    data: Any = None,
    message: str = "Success",
    code: int = BizCode.SUCCESS,
    request: Optional[Request] = None,
    trace_id: Optional[str] = None,
) -> dict[str, Any]:
    """构建成功响应字典。"""
    return ApiResponse(
        code=code,
        message=message,
        data=data,
        trace_id=_resolve_trace_id(request, trace_id),
    ).model_dump(exclude_none=True)


def error(
    *,
    code: int,
    message: str,
    detail: Any = None,
    request: Optional[Request] = None,
    trace_id: Optional[str] = None,
) -> dict[str, Any]:
    """构建错误响应字典。

    ``detail`` 嵌套在 ``data.detail`` 中，保持与成功响应结构对称。
    """
    data_payload: dict[str, Any] | None = None
    if detail is not None:
        data_payload = {"detail": detail}

    return ApiResponse(
        code=code,
        message=message,
        data=data_payload,
        trace_id=_resolve_trace_id(request, trace_id),
    ).model_dump(exclude_none=True)


def paginated(
    *,
    items: list,
    total: int,
    message: str = "Success",
    request: Optional[Request] = None,
    trace_id: Optional[str] = None,
) -> dict[str, Any]:
    """构建分页成功响应字典。

    ``data`` 中包含 ``items`` 列表和 ``total`` 总数。
    """
    return ApiResponse(
        code=BizCode.SUCCESS,
        message=message,
        data={"items": items, "total": total},
        trace_id=_resolve_trace_id(request, trace_id),
    ).model_dump(exclude_none=True)
