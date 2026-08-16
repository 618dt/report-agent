"""
    report.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    分析报告产物协议。

"""
from __future__ import annotations

from app.agent.tools.local.begin_report import BEGIN_REPORT_TOOL
from app.agent.artifacts.protocol import ArtifactProtocol, register_protocol

REPORT_WRITE_PROMPT = """请根据对话中已确认的章节目录与检索资料，输出完整报告 Markdown 正文。

强制要求：
1. 只输出报告正文（含标题、章节、参考来源），不要前言、不要解释写作过程、不要调用任何工具。
2. 仅覆盖用户最终确认且 selected=true 的章节；遵守用户给出的字数/篇幅要求（约 ±20%）。
3. 正文每个来自检索的事实/数据句末必须有 [N]，例如：xxx [1]
   禁止只在文末列参考来源。文末标题必须是「## 参考来源」。
   每条必须写成「N. 标题 - https://...」，URL 必须来自提示中的可用来源清单。
   只列出正文实际用 [N] 引用过的来源；禁止无链接书目。
4. 禁止在 thinking 中起草正文；本轮输出即为最终报告。
"""

REPORT_PROTOCOL = register_protocol(
    ArtifactProtocol(
        kind="report",
        begin_tool=BEGIN_REPORT_TOOL,
        write_prompt=REPORT_WRITE_PROMPT,
        plan_complete_note="报告已提交",
    ),
)
