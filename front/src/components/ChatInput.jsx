import { useState, useRef, useEffect } from 'react'
import { Brain, ListTodo, Send, Square } from 'lucide-react'
import './ChatInput.css'

export default function ChatInput({ onSend, onCancel, loading }) {
  const [text, setText] = useState('')
  const [deepThinking, setDeepThinking] = useState(false)
  const [planMode, setPlanMode] = useState(false)
  const inputRef = useRef(null)

  useEffect(() => {
    if (!loading && inputRef.current) {
      inputRef.current.focus()
    }
  }, [loading])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || loading) return
    onSend(trimmed, {
      deep_thinking: deepThinking,
      plan_mode: planMode,
    })
    setText('')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="chat-input-bar">
      <div className="chat-input-wrap">
        <textarea
          ref={inputRef}
          className="chat-input"
          placeholder="输入您的问题... (Enter 发送，Shift+Enter 换行)"
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
          onInput={(e) => {
            // auto-resize
            e.target.style.height = 'auto'
            e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
          }}
        />
        <div className="chat-input-actions">
          <button
            type="button"
            className={`btn-mode-toggle${planMode ? ' is-on' : ''}`}
            onClick={() => setPlanMode((v) => !v)}
            disabled={loading}
            title={planMode ? 'Plan 模式已开启（先规划再执行）' : 'Plan 模式已关闭'}
            aria-pressed={planMode}
          >
            <ListTodo size={14} />
            <span>Plan</span>
          </button>
          <button
            type="button"
            className={`btn-mode-toggle${deepThinking ? ' is-on' : ''}`}
            onClick={() => setDeepThinking((v) => !v)}
            disabled={loading}
            title={deepThinking ? '深度思考已开启' : '深度思考已关闭（闲聊不思考）'}
            aria-pressed={deepThinking}
          >
            <Brain size={14} />
            <span>深度思考</span>
          </button>
          {loading ? (
            <button className="btn-cancel" onClick={onCancel} title="停止">
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button
              className="btn-send"
              onClick={handleSend}
              disabled={!text.trim()}
              title="发送"
            >
              <Send size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
