/**
 * HITL 工具调用审批面板
 * 展示待审批的工具列表，提供批准/拒绝按钮。
 */
import { Check, X, AlertTriangle } from 'lucide-react'
import './ApprovalPanel.css'

export default function ApprovalPanel({ tools, onApprove, onDeny }) {
  if (!tools || !_isMeaningful(tools)) return null

  const toolList = _parseTools(tools)

  return (
    <div className="approval-panel">
      <div className="approval-header">
        <AlertTriangle size={16} className="approval-warn-icon" />
        <div>
          <div className="approval-title">工具调用确认</div>
          <div className="approval-subtitle">Agent 请求执行以下工具</div>
        </div>
      </div>
      <div className="approval-tools">
        {toolList.map((tool, i) => (
          <div key={i} className="approval-tool-item">
            <div className="approval-tool-name">{tool.name}</div>
            {tool.args && Object.keys(tool.args).length > 0 && (
              <div className="approval-tool-args">
                {Object.entries(tool.args).map(([k, v]) => (
                  <span key={k} className="approval-tool-arg">
                    <span className="arg-key">{k}</span>={' '}
                    <span className="arg-value">{String(v)}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="approval-actions">
        <button className="approval-btn approve" onClick={() => onApprove?.()}>
          <Check size={14} />
          批准
        </button>
        <button className="approval-btn deny" onClick={() => onDeny?.()}>
          <X size={14} />
          拒绝
        </button>
      </div>
    </div>
  )
}

function _isMeaningful(data) {
  if (!data) return false
  if (Array.isArray(data) && data.length === 0) return false
  return true
}

function _parseTools(data) {
  if (Array.isArray(data)) return data
  try {
    const parsed = typeof data === 'string' ? JSON.parse(data) : data
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}
