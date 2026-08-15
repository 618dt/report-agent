"""
    middleware.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    技能中间件：将技能列表注入系统提示，注册 load_skill 工具

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

from typing import Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain.tools import tool

from app.agent.skills.loader import SKILLS, get_skill_content


@tool
def load_skill(skill_name: str) -> str:
    """Load the full content of a skill into the agent's context.

    Use this when you need detailed information about how to handle a specific
    type of request. This will provide you with comprehensive instructions,
    policies, and guidelines for the skill area.

    Args:
        skill_name: The name of the skill to load
            (e.g., "report-generator")
    """
    content = get_skill_content(skill_name)
    if content:
        return f"Loaded skill: {skill_name}\n\n{content}"

    available = ", ".join(s["name"] for s in SKILLS)
    return (
        f"Skill '{skill_name}' not found. "
        f"Available skills: {available}"
    )


class SkillMiddleware(AgentMiddleware):
    """Middleware that injects skill descriptions into the system prompt.

    At initialization, builds a skills prompt from the global SKILLS list.
    On each model call, appends the skills prompt to the system message.
    """

    # Register the load_skill tool as a class variable
    tools = [load_skill]

    def __init__(self) -> None:
        """Initialize and generate the skills prompt from SKILLS."""
        skills_list: list[str] = []
        for skill in SKILLS:
            skills_list.append(
                f"- **{skill['name']}**: {skill['description']}"
            )
        self.skills_prompt = "\n".join(skills_list)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Async: Inject skill descriptions into system prompt.

        Arguments:
            request {ModelRequest} -- 当前的模型请求
            handler {Callable} -- 下一个处理器

        Returns:
            ModelResponse -- 模型响应
        """
        if not self.skills_prompt:
            return await handler(request)

        # Build the skills addendum
        skills_addendum = (
            f"\n\n## Available Skills\n\n{self.skills_prompt}\n\n"
            "Use the `load_skill` tool when you need detailed information "
            "about handling a specific type of request."
        )

        # Append skills text to existing system prompt
        existing_prompt = request.system_prompt or ""
        modified_request = request.override(
            system_prompt=existing_prompt + skills_addendum
        )

        return await handler(modified_request)
