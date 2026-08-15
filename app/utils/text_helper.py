"""
    text_helper.py
    ~~~~~~~~~~~~
    文本统计等轻量工具函数。

"""
from __future__ import annotations

import re

# 基本汉字（与常见「字数」口径一致，不含标点/英文/Markdown 符号）
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def count_chinese_chars(text: str) -> int:
    """统计文本中的汉字数量。

    Arguments:
        text -- 任意字符串（可为 Markdown）

    Returns:
        int -- 汉字个数；非字符串或空串返回 0
    """
    if not text or not isinstance(text, str):
        return 0
    return len(_CJK_CHAR_RE.findall(text))
