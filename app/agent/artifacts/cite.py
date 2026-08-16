"""
    cite.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    从 web_search / web_fetch 抽出带来源 URL 的清单，提交前强制写回报告。

"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from langchain_core.messages import ToolMessage

from app.agent.artifacts.protocol import _message_text

_SOURCES_JSON_MARK = "SOURCES_JSON:"
_SOURCES_HEADING_RE = re.compile(
    r"(?:^|\n)#{1,3}[^\n]*参考来源[^\n]*\n[\s\S]*?(?=\n#{1,3}\s+\S|\n---\s*$|$)",
    re.I,
)
_FETCH_URL_RE = re.compile(
    r"from\s+(https?://\S+?)(?::|\s|$)",
    re.I,
)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"(https?://\S+)")
_LINE_NUMBERED_RE = re.compile(r"^(\d+)[\.\)、．]\s*(.+)$")
_LINE_BRACKET_RE = re.compile(r"^\[(\d+)\]\s*(.+)$")
_CITE_RE = re.compile(r"\[(\d+)\]")
_MD_LINK_PROTECT_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_TITLE_TAIL_RE = re.compile(r"[，,][^，,]*$|第[\d\-—~至到]+页.*$")


def extract_search_catalog(messages: list[Any] | None) -> list[dict[str, str]]:
    """从工具结果抽出去重后的 title+url（web_search 的 SOURCES_JSON / web_fetch URL）。"""
    catalog: list[dict[str, str]] = []
    seen: set[str] = set()
    for msg in messages or []:
        text = _message_text(msg)
        name = str(getattr(msg, "name", "") or "")
        if isinstance(msg, ToolMessage) and name == "web_fetch":
            match = _FETCH_URL_RE.search(text)
            if match:
                _push_source(catalog, seen, match.group(1), match.group(1))
            continue
        _push_sources_json(catalog, seen, text)
        if isinstance(msg, ToolMessage) and name == "web_search":
            continue
        if name == "web_fetch":
            match = _FETCH_URL_RE.search(text)
            if match:
                _push_source(catalog, seen, match.group(1), match.group(1))
    return catalog


def format_catalog_block(catalog: list[dict[str, str]]) -> str:
    """写入撰写提示：强制 [N] 使用清单编号，文末必须带 URL。"""
    if not catalog:
        return (
            "\n本轮没有 web_search / web_fetch 来源，"
            "不要编造参考来源，也不要伪造 URL。"
        )
    lines = [
        "正文里每一个来自检索的事实、数据、政策、财报或估值判断，句末必须紧跟 [N]。",
        "正确示例：xxx [1]",
        "禁止只在文末列参考来源、正文却没有任何 [N]。",
        "行内 [N] 的 N 必须是下表编号。",
        "文末标题必须是「## 参考来源」。每条格式必须是：",
        "N. 标题 - https://完整URL",
        "禁止无链接书目（不要只写「XX年报第12页」却不附 URL）。",
        "只列出正文实际引用过的编号，不要把整张清单粘贴进文末。",
        "",
        "【可用来源清单】",
    ]
    for i, item in enumerate(catalog, 1):
        lines.append(f"{i}. {item['title']}\n   {item['url']}")
    return "\n" + "\n".join(lines)


def enforce_report_citations(
    markdown: str,
    catalog: list[dict[str, str]],
) -> str:
    """用检索清单给参考来源补 URL，并按正文引用顺序重新编号。"""
    text = (markdown or "").strip()
    if not text or not catalog:
        return text

    refs = _parse_ref_section(text)
    body = _SOURCES_HEADING_RE.sub("\n", text).strip()
    cited = _cited_indices_in_order(body)
    if not cited:
        cited = sorted(refs.keys())
    if not cited:
        return text

    resolved: list[dict[str, str]] = []
    old_to_new: dict[int, int] = {}
    url_to_new: dict[str, int] = {}

    for old_n in cited:
        item = _resolve_source(old_n, refs.get(old_n), catalog)
        if not item:
            continue
        url = item["url"]
        if url in url_to_new:
            old_to_new[old_n] = url_to_new[url]
            continue
        new_n = len(resolved) + 1
        resolved.append(item)
        old_to_new[old_n] = new_n
        url_to_new[url] = new_n

    if not resolved:
        return text

    new_body = _remap_citations(body, old_to_new)
    ref_lines = [
        f"{i}. {item['title']} - {item['url']}"
        for i, item in enumerate(resolved, 1)
    ]
    return f"{new_body.rstrip()}\n\n## 参考来源\n\n" + "\n".join(ref_lines) + "\n"


def _push_sources_json(
    catalog: list[dict[str, str]],
    seen: set[str],
    text: str,
) -> None:
    idx = (text or "").rfind(_SOURCES_JSON_MARK)
    if idx < 0:
        return
    raw = text[idx + len(_SOURCES_JSON_MARK):].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(parsed, list):
        return
    for item in parsed:
        if not isinstance(item, dict):
            continue
        _push_source(
            catalog,
            seen,
            str(item.get("title") or item.get("url") or ""),
            str(item.get("url") or ""),
        )


def _push_source(
    catalog: list[dict[str, str]],
    seen: set[str],
    title: str,
    url: str,
) -> None:
    url = (url or "").strip().rstrip(").,;]")
    if not url.startswith("http") or url in seen:
        return
    seen.add(url)
    catalog.append({
        "title": (title or url).strip() or url,
        "url": url,
    })


def _parse_ref_line(rest: str) -> dict[str, str]:
    md_link = _MD_LINK_RE.match(rest)
    if md_link:
        return {"title": md_link.group(1).strip(), "url": md_link.group(2).strip()}
    url_match = _BARE_URL_RE.search(rest)
    if url_match:
        url = url_match.group(1).rstrip(").,;]")
        title = rest.replace(url_match.group(1), "").strip(" -\u2014\u2013:：|") or url
        return {"title": title, "url": url}
    return {"title": rest.strip(), "url": ""}


def _parse_ref_section(markdown: str) -> dict[int, dict[str, str]]:
    match = re.search(
        r"(?:^|\n)#{1,3}[^\n]*参考来源[^\n]*\n([\s\S]*)$",
        markdown,
        re.I,
    )
    if not match:
        return {}
    refs: dict[int, dict[str, str]] = {}
    auto = 0
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if not line:
            continue
        numbered = _LINE_NUMBERED_RE.match(line)
        bracketed = _LINE_BRACKET_RE.match(line)
        if numbered:
            index = int(numbered.group(1))
            rest = numbered.group(2).strip()
        elif bracketed:
            index = int(bracketed.group(1))
            rest = bracketed.group(2).strip()
        elif line.startswith(("- ", "* ", "+ ")):
            auto += 1
            index = auto
            rest = line[2:].strip()
        else:
            continue
        if index >= 1:
            refs[index] = _parse_ref_line(rest)
    return refs


def _cited_indices_in_order(body: str) -> list[int]:
    protected: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"__LINK_{len(protected) - 1}__"

    text = _MD_LINK_PROTECT_RE.sub(_hold, body or "")
    seen: set[int] = set()
    ordered: list[int] = []
    for match in _CITE_RE.finditer(text):
        n = int(match.group(1))
        if n >= 1 and n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def _remap_citations(body: str, old_to_new: dict[int, int]) -> str:
    protected: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"__LINK_{len(protected) - 1}__"

    text = _MD_LINK_PROTECT_RE.sub(_hold, body or "")

    def _repl(match: re.Match[str]) -> str:
        n = int(match.group(1))
        new_n = old_to_new.get(n)
        return f"[{new_n}]" if new_n else match.group(0)

    text = _CITE_RE.sub(_repl, text)
    return re.sub(
        r"__LINK_(\d+)__",
        lambda m: protected[int(m.group(1))],
        text,
    )


def _normalize_title(title: str) -> str:
    text = _TITLE_TAIL_RE.sub("", title or "")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text).lower()


def _title_score(query: str, candidate: str) -> float:
    q = _normalize_title(query)
    t = _normalize_title(candidate)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    if q in t or t in q:
        return 0.92
    return SequenceMatcher(None, q, t).ratio()


def _best_catalog_match(
    title: str,
    catalog: list[dict[str, str]],
    *,
    min_score: float = 0.46,
) -> dict[str, str] | None:
    best: dict[str, str] | None = None
    best_score = min_score
    for item in catalog:
        score = _title_score(title, item["title"])
        if score > best_score:
            best = item
            best_score = score
    return best


def _resolve_source(
    index: int,
    ref: dict[str, str] | None,
    catalog: list[dict[str, str]],
) -> dict[str, str] | None:
    if ref and ref.get("url"):
        url = ref["url"]
        for item in catalog:
            if item["url"] == url:
                title = ref.get("title") or item["title"]
                return {"title": title, "url": url}
        return {"title": ref.get("title") or url, "url": url}
    if ref and ref.get("title"):
        matched = _best_catalog_match(ref["title"], catalog)
        if matched:
            return {
                "title": ref["title"] or matched["title"],
                "url": matched["url"],
            }
    if 1 <= index <= len(catalog) and not ref:
        return catalog[index - 1]
    return None
