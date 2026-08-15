import { useEffect, useRef } from 'react'
import MessageBubble from './MessageBubble.jsx'
import ChatInput from './ChatInput.jsx'
import AgentTrace, { hasVisibleTrace } from './AgentTrace.jsx'
import InteractivePanel from './InteractivePanel.jsx'
import PlanProgressCard from './PlanProgressCard.jsx'
import ConfirmResultCard, { extractConfirmResultsFromEvents } from './ConfirmResultCard.jsx'
import ReportCard from './ReportCard.jsx'
import { resolveSources } from './SourcesPanel.jsx'
import './ChatWindow.css'

/** 距底部多少像素内视为「仍贴底」，可恢复自动滚动 */
const STICK_BOTTOM_THRESHOLD_PX = 80

function formatTokenCount(n) {
  const num = Number(n) || 0
  if (num >= 10000) return `${(num / 1000).toFixed(1)}k`
  if (num >= 1000) return `${(num / 1000).toFixed(1)}k`
  return String(num)
}

function TokenUsageBar({ usage }) {
  if (!usage || typeof usage !== 'object') return null
  const total = Number(usage.total_tokens) || 0
  if (total <= 0) return null
  const input = Number(usage.input_tokens) || 0
  const output = Number(usage.output_tokens) || 0
  const estimated = Boolean(usage.estimated)
  const label = estimated ? `~${formatTokenCount(total)}` : formatTokenCount(total)
  const title = [
    `输入 ${input.toLocaleString()}`,
    `输出 ${output.toLocaleString()}`,
    `合计 ${total.toLocaleString()}`,
    estimated ? '估算中，完成后将校正' : null,
  ].filter(Boolean).join(' · ')

  return (
    <div className={`token-usage${estimated ? ' token-usage--estimated' : ''}`} title={title}>
      <span className="token-usage-label">{label} tokens</span>
      {(input > 0 || output > 0) && (
        <span className="token-usage-detail">
          ↑{formatTokenCount(input)} ↓{formatTokenCount(output)}
        </span>
      )}
    </div>
  )
}

export default function ChatWindow({
  messages,
  loading,
  conversationTitle,
  onSend,
  onCancel,
  onHitlRespond,
  onOpenReport,
}) {
  const hasContent = messages.length > 0 || loading
  const headerTitle = conversationTitle?.trim() || '新对话'
  const messagesRef = useRef(null)
  const stickToBottomRef = useRef(true)

  // 用户滚动离开底部则停止自动跟随；滚回底部附近则恢复
  useEffect(() => {
    const el = messagesRef.current
    if (!el) return undefined

    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight
      stickToBottomRef.current = distance <= STICK_BOTTOM_THRESHOLD_PX
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  // 新一轮生成开始时重新贴底
  useEffect(() => {
    if (loading) stickToBottomRef.current = true
  }, [loading])

  // 内容变化时，若仍贴底则滚到最新生成位置
  useEffect(() => {
    if (!stickToBottomRef.current) return
    const el = messagesRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, loading])

  return (
    <div className="chat-window">
      <header className="chat-header">
        <div className="chat-header-main">
          <span className="chat-header-label">当前会话</span>
          <h1 className="chat-header-title" title={headerTitle}>
            {headerTitle}
          </h1>
        </div>
      </header>

      <div className="chat-messages" ref={messagesRef}>
        {!hasContent && (
          <div className="chat-welcome">
            <h2>有什么可以帮助你的？</h2>
            <p>输入问题开始对话，AI Agent 将为你提供帮助</p>
          </div>
        )}

        {messages.map((msg, i) => {
          const msgSources = msg.role === 'assistant'
            ? resolveSources(msg.content, msg.events)
            : []
          const artifacts = msg.artifacts || []
          const reportDrafts = msg.reportDrafts || {}
          const artifactIds = new Set(
            artifacts.map(a => a.tool_call_id).filter(Boolean),
          )
          const generatingDrafts = artifacts.length > 0
            ? []
            : Object.values(reportDrafts).filter(
              d => (d.status === 'generating' || d.status === 'failed')
                && d.tool_call_id
                && !artifactIds.has(d.tool_call_id),
            )
          const showHitl = msg.role === 'assistant'
            && msg.status === 'interrupted'
            && msg.interrupt
          const showPlanProgress = msg.role === 'assistant'
            && msg.plan
            && Array.isArray(msg.plan.steps)
            && msg.plan.steps.length > 0
            && !(showHitl && (
              msg.interrupt?.reason === 'plan_confirm'
              || msg.interrupt?.schema?.type === 'plan_confirm'
            ))
          const confirmResults = msg.role === 'assistant' && !showHitl
            ? (msg.confirmResults || extractConfirmResultsFromEvents(msg.events))
            : []
          const hasReportBlocks = artifacts.length > 0 || generatingDrafts.length > 0
          const bubbleHidden = msg.role === 'assistant' && (
            showHitl
            || generatingDrafts.length > 0
            || (!msg.content && hasReportBlocks)
          )
          const showTrace = msg.role === 'assistant' && hasVisibleTrace(msg.events)
          // 有工具调用时头像贴在 trace 旁；否则仍由 MessageBubble 渲染在气泡旁
          const liftAvatar = showTrace || showPlanProgress || confirmResults.length > 0

          const bubble = (
            <MessageBubble
              message={{
                sender_id: msg.role === 'assistant' ? 'agent' : 'user',
                content: msg.content,
              }}
              isStreaming={msg.status === 'streaming'}
              sources={msgSources}
              hideAvatar={liftAvatar}
              hidden={bubbleHidden}
            />
          )

          const assistantExtras = msg.role === 'assistant' && (
            <>
              <AgentTrace events={msg.events} status={msg.status} />
              {confirmResults.map((result) => (
                <ConfirmResultCard key={result.key} result={result} />
              ))}
              {showPlanProgress && <PlanProgressCard plan={msg.plan} />}
              {bubble}
              {showHitl && (
                <InteractivePanel
                  interrupt={msg.interrupt}
                  onRespond={onHitlRespond}
                />
              )}
              {generatingDrafts.map((draft) => (
                <ReportCard
                  key={`draft_${draft.tool_call_id}`}
                  artifact={draft}
                  status={draft.status}
                  onOpen={() => onOpenReport?.(msg.run_id, draft.tool_call_id)}
                />
              ))}
              {artifacts.map((art, idx) => (
                <ReportCard
                  key={art.tool_call_id || idx}
                  artifact={art}
                  status="ready"
                  onOpen={() => onOpenReport?.(msg.run_id, art.tool_call_id)}
                />
              ))}
              <TokenUsageBar usage={msg.usage} />
            </>
          )

          if (msg.role === 'assistant') {
            return (
              <div
                key={msg.id || i}
                className={`message-wrapper${liftAvatar ? ' message-wrapper--agent' : ''}`}
              >
                {liftAvatar ? (
                  <>
                    <div className="msg-avatar msg-avatar--agent">AI</div>
                    <div className="message-body">{assistantExtras}</div>
                  </>
                ) : (
                  assistantExtras
                )}
              </div>
            )
          }

          return (
            <div key={msg.id || i} className="message-wrapper">
              {bubble}
            </div>
          )
        })}

        {loading && (!messages.length || messages[messages.length - 1]?.role === 'user') && (
          <div className="loading-indicator">
            <span className="loading-dot" />
            <span className="loading-dot" />
            <span className="loading-dot" />
          </div>
        )}
      </div>

      <ChatInput
        onSend={onSend}
        onCancel={onCancel}
        loading={loading}
      />
    </div>
  )
}
