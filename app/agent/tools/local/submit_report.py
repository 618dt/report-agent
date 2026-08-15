"""
    submit_report.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    提交报告正文：报告 Markdown 经此工具提交，由后端发 artifact SSE，与对话气泡分离。

"""
from __future__ import annotations

from langchain.tools import tool

from app.utils.log import logger
from app.utils.text_helper import count_chinese_chars

# 工具名常量，供 chat 流式层识别
SUBMIT_REPORT_TOOL = "submit_report"


@tool
def submit_report(
    title: str,
    topic: str,
    markdown: str,
) -> str:
    """提交最终分析报告正文。

    须先调用 begin_report，并将完整报告 Markdown 作为助手正文输出后，
    再调用本工具提交同一份 markdown。不要在普通对话说明中粘贴完整报告。

    Arguments:
        title -- 报告标题，如「中国A股半导体芯片分析报告」
        topic -- 报告主题/领域名称
        markdown -- 完整报告正文（Markdown，含标题、章节、参考来源）

    Returns:
        str -- 提交结果说明，供模型向用户简要确认（含准确汉字字数）
    """
    body = (markdown or "").strip()
    if not body:
        return "错误：报告正文为空，请重新生成后再次调用本工具提交。"

    report_title = (title or "").strip() or (topic or "").strip() or "分析报告"
    zh_count = count_chinese_chars(body)

    logger.info({
        "msg": "submit_report",
        "title": report_title,
        "topic": topic,
        "zh_chars": zh_count,
        "raw_chars": len(body),
    })

    return (
        f"报告《{report_title}》已提交（汉字约 {zh_count} 字）。"
        "请用一两句话告知用户报告已生成，字数必须使用本句中的汉字数，禁止自行估算或编造；"
        "不要再重复输出报告全文。"
    )
