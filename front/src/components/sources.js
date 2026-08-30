/**
 * 来源列表解析（纯函数，不依赖 React，避免与组件互相 import 导致白屏）
 */

/** 匹配「## 参考来源」以及模型常见变体（## 八、参考来源 / ## 8. 参考来源） */
const SOURCES_HEADING_RE =
  /(?:^|\n)#{1,3}[^\n]*参考来源[^\n]*\n([\s\S]*?)(?=\n#{1,3}\s+\S|\n---\s*$|$)/i

export function stripSourcesSection(text) {
  if (!text || typeof text !== 'string') return text || ''
  return text
    .replace(SOURCES_HEADING_RE, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function parseSourceRest(rest) {
  const mdLink = rest.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/)
  if (mdLink) {
    return { title: mdLink[1].trim(), url: mdLink[2].trim() }
  }
  const urlMatch = rest.match(/(https?:\/\/\S+)/)
  if (urlMatch) {
    const url = urlMatch[1].replace(/[.,;:)\]]+$/, '')
    const title = rest.replace(urlMatch[1], '').replace(/[\s\-—–:：|]+$/g, '').trim() || url
    return { title, url }
  }
  const title = rest.trim()
  return title ? { title, url: '' } : null
}

function parseSourceLine(line, autoIndex) {
  const numbered = line.match(/^(\d+)[\.\)、．]\s*(.+)$/)
  if (numbered) {
    return { index: parseInt(numbered[1], 10), rest: numbered[2].trim(), autoIndex }
  }
  const bracketed = line.match(/^\[(\d+)\]\s*(.+)$/)
  if (bracketed) {
    return { index: parseInt(bracketed[1], 10), rest: bracketed[2].trim(), autoIndex }
  }
  const bullet = line.match(/^[-*+]\s+(.+)$/)
  if (bullet) {
    return { index: autoIndex + 1, rest: bullet[1].trim(), autoIndex: autoIndex + 1 }
  }
  return null
}

/**
 * 解析正文末尾「参考来源」列表。
 * 支持 1. / [1] / - 列表；允许无 URL 的书目条目。
 */
export function extractSourcesFromContent(content) {
  if (!content || typeof content !== 'string') return []

  const sectionMatch = content.match(SOURCES_HEADING_RE)
  if (!sectionMatch) return []

  const section = sectionMatch[1]
  const byIndex = new Map()
  let autoIndex = 0

  for (const rawLine of section.split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    const parsedLine = parseSourceLine(line, autoIndex)
    if (!parsedLine) continue
    autoIndex = parsedLine.autoIndex
    if (parsedLine.index < 1) continue
    const parsed = parseSourceRest(parsedLine.rest)
    if (parsed) byIndex.set(parsedLine.index, parsed)
  }

  if (byIndex.size === 0) return []

  const maxIndex = Math.max(...byIndex.keys())
  const sources = []
  for (let i = 1; i <= maxIndex; i++) {
    sources.push(byIndex.get(i) || { title: '', url: '' })
  }
  return sources.some((s) => s.title || s.url) ? sources : []
}

export function extractCitedIndices(content) {
  if (!content || typeof content !== 'string') return new Set()
  const body = stripSourcesSection(content)
  const protected_ = body.replace(/\[([^\]]+)\]\(([^)]+)\)/g, ' ')
  const cited = new Set()
  const matches = protected_.match(/\[(\d+)\]/g) || []
  for (const token of matches) {
    const n = parseInt(token.slice(1, -1), 10)
    if (n >= 1) cited.add(n)
  }
  return cited
}

export function filterSourcesByCitations(content, sources) {
  if (!Array.isArray(sources) || sources.length === 0) return sources || []
  const cited = extractCitedIndices(content)
  if (cited.size === 0) return sources
  const maxCited = Math.max(...cited)
  return sources.slice(0, maxCited)
}

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
    const toolName = payload.name || payload.tool_response?.name || ''
    const content = payload.content_preview || payload.tool_response?.content || payload.content || ''

    if (!toolName || !content) continue

    if (toolName === 'web_search') {
      const fromJson = parseSourcesJson(content)
      if (fromJson.length > 0) {
        fromJson.forEach(pushSource)
        continue
      }

      const lines = content.split('\n')
      let currentSource = null

      for (const line of lines) {
        const trimmed = line.trim()
        const numberMatch = trimmed.match(/^(\d+)\.\s+(.+)/)
        if (numberMatch) {
          if (currentSource && currentSource.url) {
            pushSource(currentSource)
          }
          const rawTitle = numberMatch[2].trim().replace(/\s*\(score=[^)]+\)\s*$/i, '')
          currentSource = { title: rawTitle, url: '' }
        } else if (currentSource && !currentSource.url && trimmed.startsWith('http')) {
          currentSource.url = trimmed
        }
      }
      if (currentSource && currentSource.url) {
        pushSource(currentSource)
      }
    } else if (toolName === 'web_fetch') {
      const urlMatch = content.match(/from\s+(https?:\/\/\S+?)(?::|\s|$)/)
      if (urlMatch) {
        const url = urlMatch[1].replace(/[.:]+$/, '')
        pushSource({ title: url, url })
      }
    }
  }

  return sources
}

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
 * 助手消息：优先正文「参考来源」；报告关闭事件回退。
 */
export function resolveSources(content, events, options = {}) {
  const allowEventFallback = options.allowEventFallback !== false
  const fromContent = extractSourcesFromContent(content)
  if (fromContent.length > 0) {
    return filterSourcesByCitations(content, fromContent)
  }
  if (!allowEventFallback) return []
  return extractSources(events)
}

export function sourceHasDisplay(source) {
  return Boolean(source && (source.url || source.title))
}
