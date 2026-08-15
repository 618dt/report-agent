/**
 * 报告流式阅读抽屉：实时 Markdown + 完成后复制
 * 左侧边框可拖拽调整宽度，方便阅读长文。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { X, Copy, Check, FileText, Loader2 } from 'lucide-react'
import MarkdownContent from './MarkdownContent.jsx'
import SourcesPanel, { resolveSources } from './SourcesPanel.jsx'
import './ReportStreamDrawer.css'

const WIDTH_STORAGE_KEY = 'report-drawer-width'
const DEFAULT_WIDTH = 560
const MIN_WIDTH = 360
const MAX_WIDTH_RATIO = 0.92

function clampWidth(width) {
  const max = Math.floor(window.innerWidth * MAX_WIDTH_RATIO)
  return Math.min(max, Math.max(MIN_WIDTH, Math.round(width)))
}

function readStoredWidth() {
  try {
    const raw = localStorage.getItem(WIDTH_STORAGE_KEY)
    const n = Number(raw)
    if (Number.isFinite(n) && n > 0) return clampWidth(n)
  } catch {
    // ignore
  }
  return clampWidth(DEFAULT_WIDTH)
}

export default function ReportStreamDrawer({ open, report, onClose }) {
  const [copied, setCopied] = useState(false)
  const [width, setWidth] = useState(DEFAULT_WIDTH)
  const [resizing, setResizing] = useState(false)
  const [entering, setEntering] = useState(false)
  const bodyRef = useRef(null)
  const stickToBottomRef = useRef(true)
  const dragRef = useRef(null)

  const status = report?.status || 'generating'
  const isGenerating = status === 'generating'
  const markdown = report?.markdown || ''
  const title = report?.title || (isGenerating ? '报告生成中…' : '分析报告')
  const topic = report?.topic || ''
  const wordCount = typeof report?.word_count === 'number'
    ? report.word_count
    : (markdown.match(/[\u4e00-\u9fff]/g) || []).length
  const sources = markdown
    ? resolveSources(markdown, report?.events || [])
    : []

  useEffect(() => {
    if (!open) {
      setEntering(false)
      return undefined
    }
    setWidth(readStoredWidth())
    setEntering(true)
    const enterTimer = window.setTimeout(() => setEntering(false), 220)
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.()
    }
    const onResize = () => {
      setWidth((w) => clampWidth(w))
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('resize', onResize)
    return () => {
      window.clearTimeout(enterTimer)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', onResize)
    }
  }, [open, onClose])

  useEffect(() => {
    if (!open || !stickToBottomRef.current || !bodyRef.current) return
    bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [open, markdown])

  const handleScroll = () => {
    const el = bodyRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    stickToBottomRef.current = distance < 80
  }

  const handleCopy = async () => {
    if (!markdown) return
    try {
      await navigator.clipboard.writeText(markdown)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // ignore
    }
  }

  const stopDragging = useCallback(() => {
    if (!dragRef.current) return
    const finalWidth = clampWidth(dragRef.current.width)
    dragRef.current = null
    setResizing(false)
    // 宽度未变时跳过 setState，减少松手瞬间多余渲染
    setWidth((prev) => (prev === finalWidth ? prev : finalWidth))
    try {
      localStorage.setItem(WIDTH_STORAGE_KEY, String(finalWidth))
    } catch {
      // ignore
    }
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }, [])

  useEffect(() => {
    if (!resizing) return undefined

    const onMove = (e) => {
      if (!dragRef.current) return
      const clientX = e.touches ? e.touches[0].clientX : e.clientX
      const next = clampWidth(window.innerWidth - clientX)
      dragRef.current.width = next
      setWidth(next)
    }

    const onUp = () => stopDragging()

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    window.addEventListener('touchmove', onMove, { passive: false })
    window.addEventListener('touchend', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      window.removeEventListener('touchmove', onMove)
      window.removeEventListener('touchend', onUp)
    }
  }, [resizing, stopDragging])

  const startDragging = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dragRef.current = { width }
    setResizing(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  if (!open) return null

  return (
    <div className={`report-drawer-root${resizing ? ' is-resizing' : ''}`}>
      <button
        type="button"
        className="report-drawer-backdrop"
        aria-label="关闭报告"
        onClick={onClose}
      />
      <aside
        className={`report-drawer${entering ? ' report-drawer--enter' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={{ width: `min(${width}px, 100vw)` }}
      >
        <div
          className="report-drawer-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="拖动调整报告宽度"
          aria-valuenow={width}
          aria-valuemin={MIN_WIDTH}
          title="拖动调整宽度"
          onMouseDown={startDragging}
          onTouchStart={startDragging}
        />
        <header className="report-drawer-header">
          <div className="report-drawer-header-left">
            {isGenerating ? (
              <Loader2 size={16} className="report-drawer-spin" strokeWidth={2} />
            ) : (
              <FileText size={16} className="report-drawer-doc" strokeWidth={2} />
            )}
            <div className="report-drawer-heading">
              <div className="report-drawer-title">{title}</div>
              <div className="report-drawer-subtitle">
                {topic
                  ? (isGenerating || !wordCount
                    ? topic
                    : `${topic} · 约 ${wordCount} 字`)
                  : (isGenerating ? '实时生成中' : (wordCount ? `约 ${wordCount} 字` : '报告正文'))}
              </div>
            </div>
          </div>
          <div className="report-drawer-actions">
            <button
              type="button"
              className={`report-drawer-icon-btn ${copied ? 'is-success' : ''}`}
              onClick={handleCopy}
              disabled={!markdown}
              title={copied ? '已复制' : '复制报告'}
              aria-label={copied ? '已复制' : '复制报告'}
            >
              {copied ? <Check size={14} strokeWidth={2.2} /> : <Copy size={14} strokeWidth={2.2} />}
            </button>
            <button
              type="button"
              className="report-drawer-icon-btn"
              onClick={onClose}
              title="关闭"
              aria-label="关闭"
            >
              <X size={15} strokeWidth={2.2} />
            </button>
          </div>
        </header>

        <div
          className="report-drawer-body"
          ref={bodyRef}
          onScroll={handleScroll}
        >
          {!markdown ? (
            <div className="report-drawer-empty">
              报告生成中，内容将在此流出…
            </div>
          ) : (
            <>
              <MarkdownContent content={markdown} sources={sources} />
              {isGenerating && <span className="report-drawer-cursor" aria-hidden="true" />}
              {!isGenerating && sources.some(s => s?.url) && (
                <SourcesPanel sources={sources} title="参考来源" />
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  )
}
