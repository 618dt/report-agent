"""
    begin_report.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    标记开始撰写报告：之后模型输出的正文将作为报告流式内容下发。

"""
from __future__ import annotations

from langchain.tools import tool

from app.utils.log import logger

# 工具名常量，供 chat 流式层识别
BEGIN_REPORT_TOOL = "begin_report"


@tool
def begin_report(title: str, topic: str) -> str:
    """开始撰写分析报告正文。

    在用户已确认章节目录、并完成必要检索之后、输出报告正文之前调用。
    调用成功后系统会进入撰写节点并自动提交，请不要再输出报告全文。

    Arguments:
        title -- 报告标题
        topic -- 报告主题/领域名称

    Returns:
        str -- 开始撰写的确认说明
    """
    report_title = (title or "").strip() or (topic or "").strip() or "分析报告"
    topic_name = (topic or "").strip()

    logger.info({
        "msg": "begin_report",
        "title": report_title,
        "topic": topic_name,
    })

    return (
        f"已开始撰写《{report_title}》。"
        "系统将自动生成完整报告 Markdown 并提交；"
        "请不要再输出报告全文。"
    )
