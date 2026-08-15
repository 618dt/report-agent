"""
    business_codes.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    统一业务状态码定义

    :author: lcg
    :date created: 2026/8/1

"""

from enum import IntEnum


class BizCode(IntEnum):
    """统一业务状态码。

    码段约定::

        0         成功
        1xxxx     业务逻辑错误
        2xxxx     认证/授权错误
        3xxxx     资源错误（不存在、冲突、被限流等）
        4xxxx     参数/校验错误
        5xxxx     服务端/基础设施错误
        9xxxx     外部服务错误
    """

    SUCCESS = 0

    # ---- 业务错误 (1xxxx) --------------------------------------------------
    BUSINESS_ERROR = 10001
    OPERATION_FAILED = 10002
    DUPLICATE_OPERATION = 10003

    # ---- 认证/授权 (2xxxx) -------------------------------------------------
    UNAUTHORIZED = 20001
    FORBIDDEN = 20003
    TOKEN_EXPIRED = 20004
    TOKEN_INVALID = 20005

    # ---- 资源错误 (3xxxx) ---------------------------------------------------
    NOT_FOUND = 30004
    CONFLICT = 30009
    TOO_MANY_REQUESTS = 30029

    # ---- 参数校验 (4xxxx) ---------------------------------------------------
    PARAM_ERROR = 40001
    VALIDATION_ERROR = 42200

    # ---- 服务端错误 (5xxxx) -------------------------------------------------
    INTERNAL_ERROR = 50000
    SERVICE_UNAVAILABLE = 50003
    DATABASE_ERROR = 50004

    # ---- 外部服务 (9xxxx) ---------------------------------------------------
    EXTERNAL_TIMEOUT = 90001
    EXTERNAL_ERROR = 90002

    @classmethod
    def is_success(cls, code: int) -> bool:
        return code == cls.SUCCESS
