"""
    main.py
    ~~~~~~~~~~~~~~~~~~~~~~~

    

    :author: lcg
    :date created: 2026/8/1

"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.utils.exception_handler import GlobalExceptionHandler
from app.utils.langfuse_tracing import init_langfuse, shutdown_langfuse
from app.utils.log import logger
from app.utils.middleware import TraceIDMiddleware
from app.utils.mongo import init_mongo, aclose_mongo, get_mongo
from app.utils.redis import init_redis, aclose_redis, get_redis
from app.utils.response import success
from app.agent import get_agent
from app.api.conversation import router as conversation_router
from app.api.chat import router as chat_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    mongo = init_mongo()
    logger.info("mongodb initialized")
    if not mongo.ping():
        logger.error("mongodb unreachable")
    redis = init_redis()
    logger.info("redis initialized")
    if not redis.ping():
        logger.error("redis unreachable")

    # Langfuse 在 Agent 预热前初始化，确保 CallbackHandler 可用
    init_langfuse()

    # 预构建 Agent（加载模型、工具和技能）
    try:
        get_agent()
        logger.info("agent pre-warmed successfully")
    except Exception as e:
        logger.error({"msg": "agent_pre_warm_failed", "error": str(e)})

    try:
        yield
    finally:
        shutdown_langfuse()
        await aclose_mongo()
        await aclose_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Report Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(TraceIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    GlobalExceptionHandler.register(app)

    app.include_router(conversation_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")

    @app.get("/health")
    def health(request: Request):
        return success(
            data={
                "status": "ok",
                "mongo": get_mongo().ping(),
                "redis": get_redis().ping(),
            },
            request=request,
        )

    return app


app = create_app()
