from __future__ import annotations

from fastapi import Request, HTTPException
from typing import Optional


def get_current_user_id(request: Request) -> str:
    """
    从request.state中获取用户ID
    """
    # 从request.state中获取user_id
    user_id = getattr(request.state, 'user_id', None)
    if user_id is None:
        # 如果没有user_id，返回游客ID
        return "guest"
    return user_id


def login(func):
    """
    登录装饰器，用于标记需要登录认证的接口
    在装饰器中解析token并设置到request.state中
    """
    from functools import wraps
    
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # 从参数中找到request对象
        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break
        if not request:
            for key, value in kwargs.items():
                if isinstance(value, Request):
                    request = value
                    break
        
        if not request:
            raise HTTPException(status_code=401, detail="Missing request object")
        
        # 解析token并设置到request.state
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # 去掉"Bearer "前缀
            # 这里可以添加token验证逻辑，但当前需求是写死
            # 设置user_id到request.state
            request.state.user_id = "guest"
        else:
            # 如果没有token，也设置游客ID
            request.state.user_id = "guest"
        
        return await func(*args, **kwargs)
    return wrapper
