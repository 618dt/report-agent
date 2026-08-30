/**
 * 参考来源面板
 *
 * 展示 AI 回答中引用的网页来源列表。
 * 默认折叠，点击标题展开。无 URL 的书目条目以纯文本展示。
 */
import { useState } from 'react'
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react'
import { sourceHasDisplay } from './sources.js'
import './SourcesPanel.css'

export default function SourcesPanel({ sources, title = '搜索结果' }) {
  const [expanded, setExpanded] = useState(false)

  if (!sources || sources.length === 0) return null

  const validSources = sources.filter(sourceHasDisplay)
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
          {validSources.map((source, i) => {
            const index = (
              <span className="source-index">{i + 1}</span>
            )
            const label = (
              <span className="source-title-text">{source.title || source.url}</span>
            )
            if (source.url) {
              return (
                <a
                  key={i}
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="source-item"
                  title={source.url}
                >
                  {index}
                  {label}
                  <ExternalLink size={12} className="source-icon" />
                </a>
              )
            }
            return (
              <span key={i} className="source-item source-item--static" title={source.title}>
                {index}
                {label}
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}
