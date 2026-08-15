/**
 * 单个工具调用卡片
 * 展示工具名称、参数和对应的执行结果。
 * 结果默认折叠显示。
 */
import { useState } from 'react'
import { ChevronDown, ChevronRight, Wrench, Check, Loader } from 'lucide-react'
import './ToolCallCard.css'

export default function ToolCallCard({ toolCall, toolResult }) {
  const [expanded, setExpanded] = useState(false)
  const { name, args } = toolCall || {}
  const hasResult = !!toolResult

  return (
    <div className="tool-call-card">
      <div className="tool-call-header" onClick={() => setExpanded(!expanded)}>
        <span className="tool-call-icon">
          {hasResult ? <Check size={14} /> : <Loader size={14} className="spin" />}
        </span>
        <span className="tool-call-name">{name || '工具调用'}</span>
        <span className="tool-call-toggle">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </div>

      {expanded && (
        <div className="tool-call-body">
          {args && Object.keys(args).length > 0 && (
            <div className="tool-call-section">
              <div className="tool-call-section-label">参数:</div>
              <div className="tool-call-args">
                {_formatToolArgs(args)}
              </div>
            </div>
          )}
          {hasResult && (
            <div className="tool-call-section">
              <div className="tool-call-section-label">结果:</div>
              <pre className="tool-call-response">{toolResult.content}</pre>
              {toolResult.truncated && (
                <div className="tool-call-truncated-hint">
                  结果已截断（原始大小: {_formatBytes(toolResult.content_size)}）
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function _formatToolArgs(args) {
  if (!args || Object.keys(args).length === 0) {
    return <span className="arg-empty">无参数</span>
  }
  return Object.entries(args).map(([k, v]) => (
    <span key={k} className="tool-call-arg">
      <span className="arg-key">{k}</span>={' '}
      <span className="arg-value">{String(v)}</span>
    </span>
  ))
}

function _formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
