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
2. 仅覆盖用户最终确认且 selected=true 的章节（见下方「用户已确认且必须写入的章节」）；遵守用户给出的字数/篇幅要求（约 ±20%）。同一处不要重复标注相同编号（禁止 [6][6]）。
3. 来源引用必须遵守系统提示中的「引用规范」：
   - 行内使用 [N]，紧跟被引用的句子或数据，例如：2024年销量同比增长35%[1]
   - 多次搜索按 URL 去重后，从 1 重新连续编号；不要沿用某一次 web_search 的局部编号
   - 文末输出「## 参考来源」，每条写成「N. 标题 - URL」，URL 必须复制自本轮检索结果
   - 禁止编造链接；禁止只写空的「## 参考来源」标题；未引用的检索结果不要列入
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
