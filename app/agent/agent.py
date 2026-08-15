"""
    agent.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    Agent 工厂：懒加载单例 Agent，使用 DeepSeek 模型 + 本地工具 + 技能系统

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from typing import Any, Optional

from langchain.agents import create_agent
from langgraph.checkpoint.mongodb import MongoDBSaver

from app.agent.deepseek_chat import ChatDeepSeekCompat
from app.agent.skills import SkillMiddleware, load_skills_from_disk
from app.agent.tools.local import (
    begin_report,
    propose_plan,
    request_user_confirmation,
    submit_report,
    update_plan_step,
    web_fetch,
    web_search,
)
from app.agent.plan_mode_middleware import PlanModeMiddleware
from app.agent.thinking_middleware import ThinkingMiddleware
from app.agent.time_middleware import CurrentTimeMiddleware
from app.configs import cluster_configs
from app.utils.log import logger
from app.utils.mongo import get_mongo

# 全局 Agent 单例
_agent: Optional[Any] = None

# 收集所有本地工具
ALL_TOOLS = [
    web_search,
    web_fetch,
    propose_plan,
    update_plan_step,
    request_user_confirmation,
    begin_report,
    submit_report,
]

# LangGraph checkpoint 集合名（与业务表隔离）
_CHECKPOINT_COLLECTION = "langgraph_checkpoints"
_CHECKPOINT_WRITES_COLLECTION = "langgraph_checkpoint_writes"


def _get_llm_config() -> dict:
    """从 cluster_configs 读取 llm.deepseek 配置

    Returns:
        dict -- LLM 配置字典，包含 model, model_provider, api_key, base_url 等
    """
    llm_config = cluster_configs.get("llm", {}).get("deepseek", {})
    if not llm_config:
        raise RuntimeError(
            "Missing 'llm.deepseek' config in cluster.configs.yaml"
        )
    return llm_config


def _create_mongo_checkpointer() -> MongoDBSaver:
    """使用业务 Mongo 连接创建持久化 Checkpointer

    复用 init_mongo() 的同步 MongoClient 与同一 database。
    不调用 checkpointer.close()，避免关掉共享客户端；
    连接生命周期由 app lifespan 的 aclose_mongo 管理。

    Returns:
        MongoDBSaver -- Mongo 持久化 checkpointer

    Raises:
        RuntimeError -- Mongo 尚未 init_mongo() 时抛出
    """
    mongo = get_mongo()
    checkpointer = MongoDBSaver(
        client=mongo.client,
        db_name=mongo.db_name,
        checkpoint_collection_name=_CHECKPOINT_COLLECTION,
        writes_collection_name=_CHECKPOINT_WRITES_COLLECTION,
    )
    logger.info({
        "msg": "mongo_checkpointer_created",
        "db_name": mongo.db_name,
        "checkpoint_collection": _CHECKPOINT_COLLECTION,
        "writes_collection": _CHECKPOINT_WRITES_COLLECTION,
    })
    return checkpointer


def get_agent():
    """懒加载创建 Agent 单例

    使用 DeepSeek 模型（OpenAI 兼容接口）+ 本地工具 + 技能中间件
    + MongoDB 持久化检查点。首次调用时构建，后续调用直接返回已构建的实例。

    应在项目启动生命周期（lifespan）中于 init_mongo() 之后预调用以完成预热。

    Returns:
        CompiledStateGraph -- LangGraph 编译后的 Agent 图
    """
    global _agent
    if _agent is not None:
        return _agent

    llm_cfg = _get_llm_config()

    # 初始化 DeepSeek 模型（OpenAI 兼容 + 保留 reasoning_content）
    # stream_usage=True：流式末包带回 usage，供实时 token 统计与 Langfuse
    model = ChatDeepSeekCompat(
        model=llm_cfg["model"],
        api_key=llm_cfg["api_key"],
        base_url=llm_cfg.get("base_url"),
        stream_usage=True,
    )

    # 系统提示，支持从配置读取
    system_prompt = llm_cfg.get(
        "system_prompt",
        "You are a helpful assistant.",
    )

    # 加载技能系统
    skills = load_skills_from_disk()
    logger.info({
        "msg": "agent_skills_loaded",
        "count": len(skills),
    })

    # 创建技能中间件 + 当前时间中间件 + 深度思考中间件 + Plan 模式中间件
    skill_middleware = SkillMiddleware()
    time_middleware = CurrentTimeMiddleware()
    thinking_middleware = ThinkingMiddleware()
    plan_mode_middleware = PlanModeMiddleware()

    # Mongo 持久化检查点（按 thread_id=conversation_id 跨进程恢复多轮上下文）
    checkpointer = _create_mongo_checkpointer()

    # 创建 Agent
    # 章节确认等 HITL 通过 request_user_confirmation / propose_plan 工具内 interrupt() 触发
    # 恢复：POST /api/chat/stream 传入 response={action, payload}（或兼容 approved）
    # middleware 顺序：思考参数 → 时间 → Plan 模式 → 技能说明
    _agent = create_agent(
        model=model,
        tools=ALL_TOOLS,
        system_prompt=system_prompt,
        middleware=[
            thinking_middleware,
            time_middleware,
            plan_mode_middleware,
            skill_middleware,
        ],
        checkpointer=checkpointer
    )

    logger.info({
        "msg": "agent_created",
        "model": llm_cfg["model"],
        "model_provider": llm_cfg.get("model_provider"),
        "tools": [t.name for t in ALL_TOOLS],
        "skills_count": len(skills),
        "checkpointer": "MongoDBSaver",
    })

    return _agent
