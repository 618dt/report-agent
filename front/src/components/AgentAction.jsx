import { Loader2, Search, Brain, Globe, FileText } from 'lucide-react'
import './AgentAction.css'

/**
 * Agent 动作指示器
 * 展示 Agent 当前正在执行的操作（搜索、思考、读取网页等）
 */
export default function AgentAction({ action, loading }) {
  if (!action && !loading) return null

  const icon = getActionIcon(action)

  return (
    <div className="agent-action">
      {loading && !action ? (
        <Loader2 size={14} className="agent-action-spin" />
      ) : (
        icon
      )}
      <span>{action || '处理中...'}</span>
    </div>
  )
}

function getActionIcon(action) {
  if (!action) return <Loader2 size={14} className="agent-action-spin" />
  const text = action.toLowerCase()
  if (text.includes('搜索') || text.includes('search')) return <Search size={14} />
  if (text.includes('思考') || text.includes('think')) return <Brain size={14} />
  if (text.includes('网页') || text.includes('fetch') || text.includes('读取')) return <Globe size={14} />
  if (text.includes('报告') || text.includes('生成') || text.includes('写')) return <FileText size={14} />
  return <Loader2 size={14} className="agent-action-spin" />
}
