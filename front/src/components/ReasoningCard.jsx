/**
 * 思考步骤卡片
 * 流式思考中自动展开正文，并跟到底部显示最新文字；结束后默认折叠可手动打开。
 */
import { useEffect, useRef, useState } from 'react'
import { Brain, Check, ChevronDown, ChevronRight, Loader } from 'lucide-react'
import './ReasoningCard.css'

const STICK_BOTTOM_THRESHOLD_PX = 40

export default function ReasoningCard({ content, streaming }) {
  const [expanded, setExpanded] = useState(Boolean(streaming))
  const contentRef = useRef(null)
  const stickToBottomRef = useRef(true)
  const text = content || ''
  const preview = text.length > 80 ? `${text.slice(0, 80)}…` : text

  useEffect(() => {
    if (streaming) {
      setExpanded(true)
      stickToBottomRef.current = true
    }
  }, [streaming])

  // 用户上滚阅读历史思考时停止跟底；滚回底部附近则恢复
  useEffect(() => {
    const el = contentRef.current
    if (!el) return undefined

    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight
      stickToBottomRef.current = distance <= STICK_BOTTOM_THRESHOLD_PX
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [expanded, streaming])

  // 流式增量：贴底时滚到最新思考文字
  useEffect(() => {
    const el = contentRef.current
    if (!el || !stickToBottomRef.current) return
    el.scrollTop = el.scrollHeight
  }, [text, streaming, expanded])

  const showBody = expanded || streaming

  return (
    <div className="reasoning-card">
      <div
        className="reasoning-card-header"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="reasoning-card-icon">
          {streaming ? (
            <Loader size={14} className="spin" />
          ) : (
            <Check size={14} />
          )}
        </span>
        <Brain size={14} className="reasoning-card-brain" />
        <span className="reasoning-card-name">
          {streaming ? '思考中…' : '思考'}
          {!showBody && preview ? (
            <span className="reasoning-card-preview"> — {preview}</span>
          ) : null}
        </span>
        <span className="reasoning-card-toggle">
          {showBody ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </div>

      {showBody && (
        <div className="reasoning-card-body">
          <pre ref={contentRef} className="reasoning-card-content">
            {text || '（暂无内容）'}
          </pre>
        </div>
      )}
    </div>
  )
}
