"""
    response.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    统一 API 响应模型

    :author: lcg
    :date created: 2026/8/1

"""

from __future__ import annotations

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, computed_field

from app.constants import BizCode

DataT = TypeVar("DataT")


class ApiResponse(BaseModel, Generic[DataT]):
    """通用 API 响应封装。

    既可作为 FastAPI ``response_model`` 使用::

        @router.get("/items/{id}", response_model=ApiResponse[ItemSchema])

    也可通过辅助函数编程式构建::

        return success(data=item)
    """

    code: int = BizCode.SUCCESS
    message: str = "Success"
    data: Optional[DataT] = None
    trace_id: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        return BizCode.is_success(self.code)

    model_config = {"json_schema_serialization_defaults_required": True}
