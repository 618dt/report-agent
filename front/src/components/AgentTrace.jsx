/**
 * Agent 执行追踪组件
 *
 * 渲染某个 run 下的所有 Agent 执行步骤（思考、工具调用、工具结果）。
 * 生成中自动展开；结束后自动收起。
 *
 * 说明：begin_report / submit_report /
 * request_user_confirmation / propose_plan
 * 不在 trace 中展开，分别由 ReportCard / InteractivePanel / ConfirmResultCard 展示。
 */
import { useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Loader } from 'lucide-react'
import ToolCallCard from './ToolCallCard.jsx'
import ReasoningCard from './ReasoningCard.jsx'
import './AgentTrace.css'

const HIDDEN_TRACE_TOOLS = new Set([
  'begin_report',
  'submit_report',
  'request_user_confirmation',
  'propose_plan',
  'update_plan_step',
])

export function hasVisibleTrace(events) {
  return _buildSteps(events).length > 0
}

export default function AgentTrace({ events, status }) {
  const steps = _buildSteps(events)
  const isLive = status === 'streaming'
  const [expanded, setExpanded] = useState(isLive)

  useEffect(() => {
    // 生成中自动展开；结束后自动收起
    setExpanded(isLive)
  }, [isLive])

  if (steps.length === 0) return null

  const allDone = steps.every((s) => {
    if (s.kind === 'reasoning') return !s.streaming
    return !!s.toolResult
  })
  const summary = _buildSummary(steps)

  return (
    <div className={`agent-trace${expanded ? ' agent-trace--open' : ''}`}>
      <button
        type="button"
        className="agent-trace-toggle"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="agent-trace-toggle-icon">
          {allDone && !isLive ? <Check size={14} /> : <Loader size={14} className="spin" />}
        </span>
        <span className="agent-trace-toggle-label">{summary}</span>
        <span className="agent-trace-toggle-chevron">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>

      {expanded && (
        <div className="agent-trace-steps">
          {steps.map((step, i) => (
            step.kind === 'reasoning' ? (
              <ReasoningCard
                key={i}
                content={step.content}
                streaming={step.streaming}
              />
            ) : (
              <ToolCallCard
                key={i}
                toolCall={step.toolCall}
                toolResult={step.toolResult}
              />
            )
          ))}
        </div>
      )}
    </div>
  )
}

function _buildSummary(steps) {
  const reasoningCount = steps.filter((s) => s.kind === 'reasoning').length
  const toolSteps = steps.filter((s) => s.kind === 'tool')
  const toolCount = toolSteps.length
  const names = [...new Set(toolSteps.map((s) => s.toolCall?.name).filter(Boolean))]

  const parts = []
  if (reasoningCount > 0) {
    parts.push(reasoningCount === 1 ? '已思考' : `思考 ${reasoningCount} 段`)
  }
  if (toolCount > 0) {
    if (names.length === 0) {
      parts.push(`已执行 ${toolCount} 个工具`)
    } else if (names.length <= 2) {
      parts.push(`已调用 ${names.join('、')}（${toolCount}）`)
    } else {
      parts.push(`已执行 ${toolCount} 个工具调用`)
    }
  }
  if (parts.length === 0) return '执行步骤'
  return parts.join(' · ')
}

function _buildSteps(events) {
  if (!events || events.length === 0) return []

  const resultByCallId = {}
  for (const evt of events) {
    if (evt.type === 'tool_result') {
      const payload = evt.payload || {}
      const callId = payload.tool_call_id || payload.tool_response?.tool_call_id
      const name = payload.name || payload.tool_response?.name || ''
      if (HIDDEN_TRACE_TOOLS.has(name)) continue
      if (callId) {
        resultByCallId[callId] = {
          content: payload.content_preview || payload.tool_response?.content || payload.content || '',
          content_size: payload.content_size ?? payload.tool_response?.content_size ?? 0,
          truncated: payload.truncated ?? payload.tool_response?.truncated ?? false,
        }
      }
    }
  }

  const steps = []
  for (const evt of events) {
    if (evt.type === 'reasoning') {
      const payload = evt.payload || {}
      const content = payload.content || payload.summary || ''
      if (!content && !payload.streaming) continue
      steps.push({
        kind: 'reasoning',
        content,
        streaming: Boolean(payload.streaming),
      })
      continue
    }

    if (evt.type === 'tool_call') {
      const payload = evt.payload || {}

      if (payload.tool_calls && Array.isArray(payload.tool_calls)) {
        for (const tc of payload.tool_calls) {
          if (HIDDEN_TRACE_TOOLS.has(tc.name)) continue
          steps.push({
            kind: 'tool',
            toolCall: tc,
            toolResult: resultByCallId[tc.id] || null,
          })
        }
      } else if (payload.tool_call) {
        const tc = payload.tool_call
        if (HIDDEN_TRACE_TOOLS.has(tc.name)) continue
        steps.push({
          kind: 'tool',
          toolCall: tc,
          toolResult: resultByCallId[tc.id] || null,
        })
      }
    }
  }

  return steps
}

/** 从 run events 提取报告 artifact 列表 */
export function extractArtifactsFromEvents(events) {
  if (!events || events.length === 0) return []
  const artifacts = []
  for (const evt of events) {
    if (evt.type !== 'artifact') continue
    const payload = evt.payload || {}
    if (payload.markdown) {
      artifacts.push(payload)
    }
  }
  return artifacts
}
