"""
    loader.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    技能加载器：从 skills/<skill_name>/SKILL.md 加载技能列表

    :author: lcg
    :date created: 2026/8/1

"""
from __future__ import annotations

import os
import re
from typing import Optional

import yaml

from app.utils.log import logger

# skills 目录的绝对路径
SKILLS_BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__))
)

# 全局技能注册表
SKILLS: list[dict] = []

# 匹配 YAML frontmatter: 以 --- 开头和结尾的部分
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL
)


def _parse_skill_md(file_path: str) -> Optional[dict]:
    """解析单个 SKILL.md 文件

    格式:
        ---
        name: skill-name
        description: skill description
        ---
        # Markdown content ...

    Arguments:
        file_path {str} -- SKILL.md 文件路径

    Returns:
        dict | None -- {"name": ..., "description": ..., "content": ...} 或 None
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        logger.error({
            "msg": "failed_to_read_skill_file",
            "path": file_path,
            "error": str(e),
        })
        return None

    match = _FRONTMATTER_RE.match(raw)
    if not match:
        logger.warning({
            "msg": "skill_missing_frontmatter",
            "path": file_path,
        })
        return None

    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        logger.error({
            "msg": "skill_frontmatter_yaml_error",
            "path": file_path,
            "error": str(e),
        })
        return None

    if not isinstance(meta, dict):
        logger.warning({
            "msg": "skill_frontmatter_not_dict",
            "path": file_path,
        })
        return None

    name = meta.get("name", "")
    description = meta.get("description", "")

    if not name:
        logger.warning({
            "msg": "skill_missing_name",
            "path": file_path,
        })
        return None

    content = match.group(2).strip()

    return {
        "name": name,
        "description": description,
        "content": content,
    }


def load_skills_from_disk(skills_dir: str = None) -> list[dict]:
    """遍历 skills 目录下的子目录，读取每个目录中的 SKILL.md

    技能目录结构:
        skills/
          <skill_name>/
            SKILL.md

    Arguments:
        skills_dir {str} -- 技能目录的绝对路径，默认使用模块所在目录

    Returns:
        list[dict] -- 技能列表 [{"name": ..., "description": ..., "content": ...}, ...]
    """
    global SKILLS

    base = skills_dir or SKILLS_BASE_DIR

    if not os.path.isdir(base):
        logger.info({
            "msg": "skills_directory_not_found",
            "path": base,
        })
        SKILLS.clear()
        return SKILLS

    loaded: list[dict] = []

    try:
        for entry in os.scandir(base):
            if not entry.is_dir():
                continue
            skill_md_path = os.path.join(entry.path, "SKILL.md")
            if not os.path.isfile(skill_md_path):
                continue
            skill = _parse_skill_md(skill_md_path)
            if skill is not None:
                loaded.append(skill)
                logger.info({
                    "msg": "skill_loaded",
                    "name": skill["name"],
                    "path": skill_md_path,
                })
    except OSError as e:
        logger.error({
            "msg": "skills_scan_error",
            "path": base,
            "error": str(e),
        })

    # 原地修改列表以保持导入引用的有效性
    SKILLS.clear()
    SKILLS.extend(loaded)
    return SKILLS


def get_skill_content(name: str) -> Optional[str]:
    """根据技能名获取技能的完整 Markdown 内容

    Arguments:
        name {str} -- 技能名

    Returns:
        str | None -- 技能内容，未找到返回 None
    """
    for skill in SKILLS:
        if skill["name"] == name:
            return skill["content"]
    return None


def build_skills_prompt() -> str:
    """构建技能列表的 System Prompt 文本

    供 SkillMiddleware 注入到系统提示中使用。

    Returns:
        str -- 技能列表 Prompt，无技能时返回空字符串
    """
    if not SKILLS:
        return ""

    lines = [
        "## Available Skills",
        "",
    ]
    for skill in SKILLS:
        lines.append(f"- **{skill['name']}**: {skill['description']}")
    lines.append("")
    lines.append(
        "Use the `load_skill` tool when you need detailed information "
        "about handling a specific type of request."
    )
    return "\n".join(lines)
