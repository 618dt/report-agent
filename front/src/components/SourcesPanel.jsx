/**
 * 参考来源面板
 *
 * 展示 AI 回答中引用的网页来源列表。
 * 优先解析回答正文中的「参考来源」列表（与行内 [N] 编号一致），
 * 否则回退到从 web_search / web_fetch 工具结果事件中提取。
 * 默认折叠，点击标题展开。
 */
import { useState } from 'react'
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react'
import './SourcesPanel.css'

export default function SourcesPanel({ sources, title = '搜索结果' }) {
  const [expanded, setExpanded] = useState(false)

  if (!sources || sources.length === 0) return null

  const validSources = sources.filter((s) => s?.url)
  if (validSources.length === 0) return null

  return (
    <div className={`sources-panel${expanded ? ' sources-panel--open' : ''}`}>
      <button
        type="button"
        className="sources-title"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span>{title}（{validSources.length}）</span>
        <span className="sources-title-chevron">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {expanded && (
        <div className="sources-list">
          {validSources.map((source, i) => (
            <a
              key={i}
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="source-item"
              title={source.url}
            >
              <span className="source-index">{i + 1}</span>
              <span className="source-title-text">{source.title || source.url}</span>
              <ExternalLink size={12} className="source-icon" />
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * 解析回答正文末尾的「参考来源」列表
 *
 * 支持常见写法：
 *   ## 参考来源
 *   1. 标题 - https://...
 *   1. 标题：https://...
 *   1. [标题](https://...)
 *   1. https://...
 *
 * Returns:
 *   按编号排序的来源数组；若正文无该列表则返回 []
 */
export function extractSourcesFromContent(content) {
  if (!content || typeof content !== 'string') return []

  const sectionMatch = content.match(
    /(?:^|\n)#{1,3}\s*参考来源\s*\n([\s\S]*?)(?=\n#{1,3}\s+\S|\n---\s*$|$)/i
  )
  if (!sectionMatch) return []

  const section = sectionMatch[1]
  const byIndex = new Map()

  for (const rawLine of section.split('\n')) {
    const line = rawLine.trim()
    if (!line) continue

    const numbered = line.match(/^(\d+)[\.\)、]\s*(.+)$/)
    if (!numbered) continue

    const index = parseInt(numbered[1], 10)
    const rest = numbered[2].trim()
    if (index < 1) continue

    let url = ''
    let title = rest

    const mdLink = rest.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/)
    if (mdLink) {
      title = mdLink[1].trim()
      url = mdLink[2].trim()
    } else {
      const urlMatch = rest.match(/(https?:\/\/\S+)/)
      if (urlMatch) {
        url = urlMatch[1].replace(/[.,;:)\]]+$/, '')
        title = rest.replace(urlMatch[1], '').replace(/[\s\-—–:：|]+$/g, '').trim() || url
      }
    }

    if (url) {
      byIndex.set(index, { title, url })
    }
  }

  if (byIndex.size === 0) return []

  const maxIndex = Math.max(...byIndex.keys())
  // 按下标对齐编号：缺号位保留空占位，避免 [4] 错位到第 3 项
  const sources = []
  for (let i = 1; i <= maxIndex; i++) {
    sources.push(byIndex.get(i) || { title: '', url: '' })
  }
  return sources.filter(s => s.url).length > 0 ? sources : []
}

/**
 * 从 events 列表中提取网页来源
 *
 * web_search 优先解析末尾 SOURCES_JSON；失败再回退编号+URL 文本格式。
 * web_fetch 返回格式:
 *   Successfully fetched N characters from https://url.com:
 */
export function extractSources(events) {
  if (!events || events.length === 0) return []

  const sources = []
  const seenUrls = new Set()

  const pushSource = (source) => {
    const url = (source?.url || '').trim()
    if (!url || seenUrls.has(url)) return
    seenUrls.add(url)
    sources.push({
      title: (source.title || url).trim(),
      url,
    })
  }

  for (const evt of events) {
    if (evt.type !== 'tool_result') continue

    const payload = evt.payload || {}
    // 兼容历史 API 和实时 SSE 两种格式
    const toolName = payload.name || payload.tool_response?.name || ''
    const content = payload.content_preview || payload.tool_response?.content || payload.content || ''

    if (!toolName || !content) continue

    if (toolName === 'web_search') {
      const fromJson = parseSourcesJson(content)
      if (fromJson.length > 0) {
        fromJson.forEach(pushSource)
        continue
      }

      // 兼容旧格式:
      // 1. Title
      //    https://url
      //    Content
      const lines = content.split('\n')
      let currentSource = null

      for (const line of lines) {
        const trimmed = line.trim()
        // 匹配编号行: "1. Title" 或 "1. Title (score=0.85)"
        const numberMatch = trimmed.match(/^(\d+)\.\s+(.+)/)
        if (numberMatch) {
          if (currentSource && currentSource.url) {
            pushSource(currentSource)
          }
          // 去掉可选的 (score=...) 后缀，避免标题污染
          const rawTitle = numberMatch[2].trim().replace(/\s*\(score=[^)]+\)\s*$/i, '')
          currentSource = { title: rawTitle, url: '' }
        }
        // 匹配 URL 行
        else if (currentSource && !currentSource.url && trimmed.startsWith('http')) {
          currentSource.url = trimmed
        }
      }
      if (currentSource && currentSource.url) {
        pushSource(currentSource)
      }
    } else if (toolName === 'web_fetch') {
      // 解析: "Successfully fetched N characters from https://url.com:"
      const urlMatch = content.match(/from\s+(https?:\/\/\S+?)(?::|\s|$)/)
      if (urlMatch) {
        const url = urlMatch[1].replace(/[.:]+$/, '')
        pushSource({ title: url, url })
      }
    }
  }

  return sources
}

/**
 * 解析 web_search 工具输出末尾的 SOURCES_JSON 契约
 *
 * 形如:
 *   ---
 *   SOURCES_JSON:[{"title":"...","url":"...","score":0.85}]
 *
 * Returns:
 *   来源数组；解析失败返回 []
 */
function parseSourcesJson(content) {
  if (!content || typeof content !== 'string') return []

  const marker = 'SOURCES_JSON:'
  const idx = content.lastIndexOf(marker)
  if (idx < 0) return []

  const raw = content.slice(idx + marker.length).trim()
  if (!raw) return []

  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((item) => item && typeof item === 'object' && item.url)
      .map((item) => ({
        title: item.title || item.url,
        url: String(item.url).trim(),
      }))
  } catch {
    return []
  }
}

/**
 * 解析本条助手消息的权威来源列表
 *
 * 优先使用正文「参考来源」章节（与模型行内 [N] 编号一致），
 * 否则回退到工具事件提取结果。
 */
export function resolveSources(content, events) {
  const fromContent = extractSourcesFromContent(content)
  if (fromContent.length > 0) return fromContent
  return extractSources(events)
}
