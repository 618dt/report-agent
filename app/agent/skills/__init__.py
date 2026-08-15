"""
    __init__.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    技能系统：加载、管理、注入 Agent 技能

    :author: lcg
    date created: 2026/8/1

"""
from app.agent.skills.loader import (
    SKILLS,
    build_skills_prompt,
    get_skill_content,
    load_skills_from_disk,
)
from app.agent.skills.middleware import SkillMiddleware, load_skill

__all__ = [
    "SKILLS",
    "SkillMiddleware",
    "build_skills_prompt",
    "get_skill_content",
    "load_skill",
    "load_skills_from_disk",
]
