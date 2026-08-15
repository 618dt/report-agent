import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import Sidebar from './components/Sidebar.jsx'
import ChatWindow from './components/ChatWindow.jsx'
import ReportStreamDrawer from './components/ReportStreamDrawer.jsx'
import { extractArtifactsFromEvents } from './components/AgentTrace.jsx'
import { extractPlanFromEvents } from './components/PlanProgressCard.jsx'
import { extractConfirmResultsFromEvents } from './components/ConfirmResultCard.jsx'
import { useSSE } from './hooks/useSSE.js'
import {
  fetchConversations,
  fetchMessages,
  fetchRunEvents,
  fetchActiveRun,
  deleteConversation,
} from './api/index.js'
import './App.css'

function upsertReportDraft(msg, toolCallId, patch) {
  const drafts = { ...(msg.reportDrafts || {}) }
  const prev = drafts[toolCallId] || {
    tool_call_id: toolCallId,
    kind: 'report',
    title: '',
    topic: '',
    markdown: '',
    status: 'generating',
  }
  drafts[toolCallId] = { ...prev, ...patch, tool_call_id: toolCallId }
  return drafts
}

/** 从 activeRun.partial_report / begin 工具事件恢复报告草稿（刷新后续看） */
function buildReportDraftsFromActiveRun(activeRun) {
  const drafts = {}
  const pr = activeRun?.partial_report
  if (pr && typeof pr === 'object' && (pr.tool_call_id || pr.markdown)) {
    const id = pr.tool_call_id || `report_${activeRun.run_id}`
    drafts[id] = {
      tool_call_id: id,
      kind: 'report',
      title: pr.title || '',
      topic: pr.topic || '',
      markdown: pr.markdown || '',
      status: pr.status === 'ready' ? 'ready' : 'generating',
    }
  }
  // 已有 begin 但尚未写出 partial_report 时，至少恢复「生成中」卡片
  if (Object.keys(drafts).length === 0 && Array.isArray(activeRun?.events)) {
    for (const ev of activeRun.events) {
      if (ev?.type !== 'tool_call') continue
      const calls = Array.isArray(ev.payload?.tool_calls)
        ? ev.payload.tool_calls
        : (Array.isArray(ev.payload) ? ev.payload : [])
      for (const tc of calls) {
        if (tc?.name !== 'begin_report') continue
        const id = tc.id || `begin_${activeRun.run_id}`
        drafts[id] = {
          tool_call_id: id,
          kind: 'report',
          title: tc.args?.title || '',
          topic: tc.args?.topic || '',
          markdown: '',
          status: 'generating',
        }
      }
    }
  }
  return drafts
}

/** 用服务端 HITL 事件替换同类型乐观写入，避免确认卡片重复 */
function _replaceOptimisticHitlEvent(events, type, data) {
  const next = [...(events || [])]
  for (let i = next.length - 1; i >= 0; i -= 1) {
    const ev = next[i]
    if (ev?.type !== type || ev?._server) continue
    if (type === 'approval') {
      const sameReason = !data.reason || ev.payload?.reason === data.reason
      const sameAction = !data.action || ev.payload?.action === data.action
      if (sameReason && sameAction) {
        next.splice(i, 1)
      }
      break
    }
    // outline：去掉最近一条乐观快照即可
    next.splice(i, 1)
    break
  }
  next.push({ type, payload: data, _server: true })
  return next
}

/** 将仍处于 streaming 的 reasoning 事件标记为结束（正文/工具开始即说明思考已结束） */
function finalizeStreamingReasoning(events) {
  if (!events || events.length === 0) return events || []
  let changed = false
  const next = events.map((evt) => {
    if (evt.type === 'reasoning' && evt.payload?.streaming) {
      changed = true
      return {
        ...evt,
        payload: { ...evt.payload, streaming: false },
      }
    }
    return evt
  })
  return changed ? next : events
}

export default function App() {
  const [conversations, setConversations] = useState([])
  const [activeConvId, setActiveConvId] = useState(null)
  const [messages, setMessages] = useState([])
  const [reportDrawer, setReportDrawer] = useState({
    open: false,
    runId: null,
    toolCallId: null,
  })

  const loadedConvRef = useRef(null)
  // 暂存 resume 所需的 run_id / conversation_id（在 interrupt 回调中设置）
  const pendingRunRef = useRef(null)
  const pendingConvRef = useRef(null)

  // ---- useSSE 回调 ----
  const callbacks = useRef({
    onRunStarted(conversationId, runId) {
      if (conversationId) {
        pendingConvRef.current = conversationId
        // 新会话在流式过程中就绑定 id，避免结束后 loadMessages 冲掉 HITL 状态
        loadedConvRef.current = conversationId
      }
      setMessages(prev => {
        if (prev.some(m => m.run_id === runId)) return prev
        return [...prev, {
          id: `tmp_${runId}`,
          role: 'assistant',
          content: '',
          run_id: runId,
          status: 'streaming',
          events: [],
          artifacts: [],
          reportDrafts: {},
          plan: null,
          interrupt: null,
          confirmResults: [],
          usage: null,
        }]
      })
    },

    onUsage(runId, data) {
      if (!data || typeof data !== 'object') return
      setMessages(prev => prev.map(msg => {
        if (msg.run_id !== runId) return msg
        return { ...msg, usage: data }
      }))
    },

    onContent(runId, content) {
      setMessages(prev => prev.map(msg => {
        if (msg.run_id === runId && msg.status === 'streaming') {
          return {
            ...msg,
            content: msg.content + content,
            // 正文开始即结束「思考中」态（不必等整轮 done）
            events: finalizeStreamingReasoning(msg.events),
          }
        }
        return msg
      }))
    },

    onReasoning(runId, data) {
      const delta = data?.delta || ''
      const content = data?.content
      setMessages(prev => prev.map(msg => {
        if (msg.run_id !== runId) return msg
        const events = [...(msg.events || [])]
        const last = events[events.length - 1]
        // 末尾已是 reasoning 即视为同一段（含已被 onContent/onToolCall
        // 提前 finalize 的情况）；否则才新开一段。
        // 否则终态 content 会在 streaming 已被清掉后再次 push，导致重复思考块。
        const lastIsReasoning = last?.type === 'reasoning'

        if (typeof content === 'string' && content) {
          // 终态：替换当前思考段占位，或在新一轮思考时追加
          if (lastIsReasoning) {
            events[events.length - 1] = {
              type: 'reasoning',
              payload: { content, streaming: false },
            }
          } else {
            events.push({
              type: 'reasoning',
              payload: { content, streaming: false },
            })
          }
          return { ...msg, events }
        }

        if (delta) {
          if (lastIsReasoning) {
            events[events.length - 1] = {
              type: 'reasoning',
              payload: {
                content: (last.payload?.content || '') + delta,
                streaming: true,
              },
            }
          } else {
            events.push({
              type: 'reasoning',
              payload: { content: delta, streaming: true },
            })
          }
          return { ...msg, events }
        }

        return msg
      }))
    },

    onToolCall(runId, data) {
      const toolCalls = Array.isArray(data) ? data : [data]
      setMessages(prev => prev.map(msg => {
        if (msg.run_id === runId) {
          return {
            ...msg,
            events: [
              ...finalizeStreamingReasoning(msg.events),
              ...toolCalls.map(tc => ({
                type: 'tool_call',
                payload: { tool_call: tc },
              })),
            ],
          }
        }
        return msg
      }))
    },

    onToolResponse(runId, data) {
      setMessages(prev => prev.map(msg => {
        if (msg.run_id === runId) {
          return {
            ...msg,
            events: [...msg.events, {
              type: 'tool_result',
              payload: { tool_response: data },
            }],
          }
        }
        return msg
      }))
    },

    onArtifactStart(runId, data) {
      const toolCallId = data?.tool_call_id
      if (!toolCallId) return
      setMessages(prev => prev.map(msg => {
        if (msg.run_id !== runId) return msg
        return {
          ...msg,
          reportDrafts: upsertReportDraft(msg, toolCallId, {
            status: 'generating',
            title: data.title || '',
            topic: data.topic || '',
            kind: data.kind || 'report',
            markdown: msg.reportDrafts?.[toolCallId]?.markdown || '',
          }),
        }
      }))
    },

    onArtifactDelta(runId, data) {
      const toolCallId = data?.tool_call_id
      if (!toolCallId || !data?.delta) return
      setMessages(prev => prev.map(msg => {
        if (msg.run_id !== runId) return msg
        const prevDraft = msg.reportDrafts?.[toolCallId]
        return {
          ...msg,
          reportDrafts: upsertReportDraft(msg, toolCallId, {
            status: 'generating',
            title: data.title || prevDraft?.title || '',
            topic: data.topic || prevDraft?.topic || '',
            markdown: (prevDraft?.markdown || '') + data.delta,
          }),
        }
      }))
    },

    onArtifact(runId, data) {
      if (!data?.markdown) return
      setMessages(prev => prev.map(msg => {
        if (msg.run_id !== runId) return msg

        const artifacts = [...(msg.artifacts || [])]
        const idx = artifacts.findIndex(
          a => a.tool_call_id && a.tool_call_id === data.tool_call_id,
        )
        if (idx >= 0) {
          artifacts[idx] = data
        } else {
          artifacts.push(data)
        }

        // 终态到达：合并/清理本轮所有 generating 草稿，避免 begin/submit id 不一致留下卡住的「生成中」卡片
        const prevDrafts = msg.reportDrafts || {}
        const draftKey = data.tool_call_id || `artifact_${Date.now()}`
        let mergedMarkdown = data.markdown
        let mergedTitle = data.title || ''
        let mergedTopic = data.topic || ''

        for (const draft of Object.values(prevDrafts)) {
          if (draft?.status !== 'generating' && draft?.tool_call_id !== draftKey) {
            continue
          }
          if (!mergedTitle && draft.title) mergedTitle = draft.title
          if (!mergedTopic && draft.topic) mergedTopic = draft.topic
          // 若终态前流式正文更长，保留更完整的一份（一般终态更全）
          if (
            draft.markdown
            && draft.markdown.length > (mergedMarkdown?.length || 0)
          ) {
            mergedMarkdown = draft.markdown
          }
        }

        const drafts = {
          [draftKey]: {
            kind: data.kind || 'report',
            tool_call_id: draftKey,
            title: mergedTitle,
            topic: mergedTopic,
            markdown: mergedMarkdown,
            status: 'ready',
          },
        }

        return {
          ...msg,
          artifacts,
          reportDrafts: drafts,
          events: [...msg.events, { type: 'artifact', payload: data }],
        }
      }))

      // 若抽屉正开着旧的 generating id，切到终态 tool_call_id
      setReportDrawer(prev => {
        if (!prev.open || (prev.runId && prev.runId !== runId)) return prev
        return {
          ...prev,
          runId,
          toolCallId: data.tool_call_id || prev.toolCallId,
        }
      })
    },

    onInterrupt(runId, data) {
      pendingRunRef.current = runId
      setMessages(prev => prev.map(msg => {
        if (msg.run_id === runId) {
          return { ...msg, status: 'interrupted', interrupt: data }
        }
        return msg
      }))
    },

    onPlan(runId, data) {
      if (!data || typeof data !== 'object') return
      setMessages(prev => prev.map(msg => {
        if (msg.run_id !== runId) return msg
        const events = [
          ...msg.events,
          { type: 'plan', payload: data },
        ]
        return {
          ...msg,
          plan: data,
          events,
          confirmResults: extractConfirmResultsFromEvents(events),
        }
      }))
    },

    onApproval(runId, data) {
      if (!data || typeof data !== 'object') return
      setMessages(prev => prev.map(msg => {
        if (msg.run_id !== runId) return msg
        const events = _replaceOptimisticHitlEvent(msg.events, 'approval', data)
        return {
          ...msg,
          events,
          confirmResults: extractConfirmResultsFromEvents(events),
        }
      }))
    },

    onOutline(runId, data) {
      if (!data || typeof data !== 'object') return
      setMessages(prev => prev.map(msg => {
        if (msg.run_id !== runId) return msg
        const events = _replaceOptimisticHitlEvent(msg.events, 'outline', data)
        return {
          ...msg,
          events,
          confirmResults: extractConfirmResultsFromEvents(events),
        }
      }))
    },

    onDone(runId, { messageId, interrupted, usage, cancelled }) {
      if (interrupted) {
        // 已经通过 onInterrupt 设置了状态；等待用户操作，不算 loading
        setMessages(prev => prev.map(msg => {
          if (msg.run_id !== runId) return msg
          return {
            ...msg,
            events: finalizeStreamingReasoning(msg.events),
            usage: usage || msg.usage,
          }
        }))
        return
      }
      setMessages(prev => prev.map(msg => {
        if (msg.run_id !== runId) return msg
        if (msg.status !== 'streaming' && !cancelled) return msg
        return {
          ...msg,
          id: messageId || msg.id,
          status: cancelled ? 'cancelled' : 'success',
          events: finalizeStreamingReasoning(msg.events),
          usage: usage || msg.usage,
        }
      }))
    },

    onError(runId, message, messageId, usage) {
      setMessages(prev => prev.map(msg => {
        if (msg.run_id === runId) {
          const drafts = { ...(msg.reportDrafts || {}) }
          for (const key of Object.keys(drafts)) {
            if (drafts[key].status === 'generating') {
              drafts[key] = { ...drafts[key], status: 'failed' }
            }
          }
          return {
            ...msg,
            id: messageId || msg.id,
            status: 'failed',
            content: msg.content || `错误: ${message}`,
            reportDrafts: drafts,
            events: finalizeStreamingReasoning(msg.events),
            usage: usage || msg.usage,
          }
        }
        return msg
      }))
    },
  }).current

  const { loading, send, resume, subscribe, cancel, detach } = useSSE(callbacks)
  const subscribeRef = useRef(subscribe)
  subscribeRef.current = subscribe

  const handleOpenReport = useCallback((runId, toolCallId) => {
    if (!toolCallId) return
    setReportDrawer({ open: true, runId, toolCallId })
  }, [])

  const handleCloseReport = useCallback(() => {
    setReportDrawer(prev => ({ ...prev, open: false }))
  }, [])

  const activeReport = (() => {
    if (!reportDrawer.toolCallId) return null
    for (const msg of messages) {
      if (reportDrawer.runId && msg.run_id !== reportDrawer.runId) continue
      const art = (msg.artifacts || []).find(
        a => a.tool_call_id === reportDrawer.toolCallId,
      )
      if (art) {
        return {
          ...art,
          status: 'ready',
          events: msg.events,
          run_id: msg.run_id,
        }
      }
      const draft = msg.reportDrafts?.[reportDrawer.toolCallId]
      if (draft) {
        return {
          ...draft,
          events: msg.events,
          run_id: msg.run_id,
        }
      }
    }
    return null
  })()

  useEffect(() => {
    loadConversations()
  }, [])

  const loadConversations = async () => {
    try {
      const res = await fetchConversations(0, 50)
      if (res.success && res.data?.items) {
        setConversations(res.data.items)
      }
    } catch (err) {
      console.error('加载会话列表失败:', err)
    }
  }

  const loadMessages = useCallback(async (convId) => {
    if (!convId || loadedConvRef.current === convId) return
    loadedConvRef.current = convId
    try {
      const res = await fetchMessages(convId, 0, 50)
      if (res.success && res.data?.items) {
        const historyMsgs = res.data.items.reverse()  // 最旧的在前

        // 提取 assistant 消息中的 run_id
        const runIds = historyMsgs
          .filter(m => m.sender_id === 'agent' && m.run_id)
          .map(m => m.run_id)

        // 批量拉取 run events（含各 run 的 usage）
        let eventsByRun = {}
        let usageByRun = {}
        if (runIds.length > 0) {
          const eventsRes = await fetchRunEvents(convId, [...new Set(runIds)])
          const eventsOk = eventsRes?.success || eventsRes?.code === 0
          if (eventsOk && eventsRes.data?.items) {
            for (const evt of eventsRes.data.items) {
              const rid = evt.run_id
              if (!eventsByRun[rid]) eventsByRun[rid] = []
              eventsByRun[rid].push(evt)
            }
          }
          if (eventsOk && Array.isArray(eventsRes.data?.runs)) {
            for (const run of eventsRes.data.runs) {
              if (run?._id && run.usage) usageByRun[run._id] = run.usage
            }
          }
        }

        // 组装消息
        const assembled = historyMsgs.map(m => {
          const role = m.sender_id === 'agent' ? 'assistant' : 'user'
          const events = (role === 'assistant' && m.run_id && eventsByRun[m.run_id])
            ? eventsByRun[m.run_id]
            : []
          // Message.status: 0=sending, 1=success, 2=fail
          let status = 'success'
          if (m.status === 2) status = 'failed'
          return {
            id: m._id,
            role,
            content: m.content,
            run_id: m.run_id || null,
            status,
            events,
            artifacts: role === 'assistant' ? extractArtifactsFromEvents(events) : [],
            reportDrafts: {},
            plan: role === 'assistant' ? extractPlanFromEvents(events) : null,
            confirmResults: role === 'assistant'
              ? extractConfirmResultsFromEvents(events)
              : [],
            interrupt: null,
            usage: (role === 'assistant' && m.run_id)
              ? (usageByRun[m.run_id] || null)
              : null,
          }
        })

        // 恢复 active run（interrupted 确认面板 / running 续订）
        let activeRun = null
        try {
          const activeRes = await fetchActiveRun(convId)
          const ok = activeRes?.success || activeRes?.code === 0
          activeRun = ok ? activeRes.data : null
          if (activeRun?.run_id) {
            pendingRunRef.current = activeRun.run_id
            pendingConvRef.current = convId
            const events = activeRun.events || []
            const status = activeRun.status === 'running' ? 'streaming' : 'interrupted'
            // 用户消息也带同一 run_id，必须只匹配 assistant，避免把 partial_content 写进用户气泡
            const assistantIdx = assembled.findIndex(
              m => m.run_id === activeRun.run_id && m.role === 'assistant',
            )
            const restoredDrafts = buildReportDraftsFromActiveRun(activeRun)
            const patch = {
              status,
              interrupt: activeRun.interrupt || null,
              events,
              artifacts: extractArtifactsFromEvents(events),
              plan: extractPlanFromEvents(events) || activeRun.plan || null,
              confirmResults: extractConfirmResultsFromEvents(events),
              usage: activeRun.usage || null,
              content: activeRun.partial_content || '',
              reportDrafts: restoredDrafts,
            }
            if (assistantIdx >= 0) {
              const prev = assembled[assistantIdx]
              assembled[assistantIdx] = {
                ...prev,
                ...patch,
                // 已有终态正文优先；否则用 partial_content
                content: prev.content || activeRun.partial_content || '',
                // 终态 artifact 已在 artifacts 时不必再挂 generating 草稿
                reportDrafts: (extractArtifactsFromEvents(events).length > 0)
                  ? {}
                  : restoredDrafts,
              }
            } else {
              assembled.push({
                id: `tmp_active_${activeRun.run_id}`,
                role: 'assistant',
                run_id: activeRun.run_id,
                ...patch,
              })
            }
          }
        } catch (err) {
          console.error('恢复 active run 失败:', err)
        }

        setMessages(assembled)

        // running：用 after_seq 续订 SSE（正文/报告草稿已铺底，只收后续增量）
        if (activeRun?.status === 'running' && activeRun.run_id) {
          const draftSeq = Number(activeRun.partial_report?.last_seq)
          const runSeq = Number(activeRun.last_seq)
          // 报告草稿水位优先，避免 after_seq=last_seq 时丢掉未写入草稿的 delta
          const afterSeq = Number.isFinite(draftSeq) && draftSeq >= 0
            ? draftSeq
            : (Number.isFinite(runSeq) ? runSeq : 0)
          subscribeRef.current?.(activeRun.run_id, afterSeq)
        }
      }
    } catch (err) {
      console.error('加载消息失败:', err)
    }
  }, [])

  useEffect(() => {
    if (activeConvId) {
      loadMessages(activeConvId)
    }
  }, [activeConvId, loadMessages])

  // 发送消息
  const handleSend = async (query, options = {}) => {
    const userMsg = {
      id: `tmp_user_${Date.now()}`,
      role: 'user',
      content: query,
      run_id: null,
      status: 'success',
      events: [],
      artifacts: [],
      reportDrafts: {},
      plan: null,
      interrupt: null,
      usage: null,
    }
    setMessages(prev => [...prev, userMsg])

    const result = await send(query, activeConvId, {
      deep_thinking: Boolean(options.deep_thinking),
      plan_mode: Boolean(options.plan_mode),
    })
    if (!result) return

    const { conversationId } = result

    if (conversationId && conversationId !== activeConvId) {
      // 流式过程中 onRunStarted 已写入 loadedConvRef，避免此处触发 loadMessages 冲掉 HITL
      loadedConvRef.current = conversationId
      pendingConvRef.current = conversationId
      setActiveConvId(conversationId)
    }

    loadConversations()
  }

  // 统一 HITL 响应
  const handleHitlRespond = async (response) => {
    const runId = pendingRunRef.current
    const convId = activeConvId || pendingConvRef.current
    if (!runId || !convId) return

    setMessages(prev => prev.map(msg => {
      if (msg.run_id === runId && msg.status === 'interrupted') {
        const next = { ...msg, status: 'streaming', interrupt: null }
        const reason = msg.interrupt?.reason
          || msg.interrupt?.schema?.type
          || ''
        // 乐观写入 approval，待 SSE 权威事件覆盖/去重
        const approvalPayload = {
          action: response?.action,
          payload: response?.payload ?? null,
          reason,
          title: msg.interrupt?.title || '',
          topic: msg.interrupt?.schema?.topic || '',
          goal: msg.interrupt?.schema?.goal || '',
        }
        next.events = [
          ...(msg.events || []),
          { type: 'approval', payload: approvalPayload },
        ]

        // Plan 确认后立即展示进度列表（后续 SSE plan 会覆盖为权威快照）
        if (
          response?.action === 'confirm'
          && (reason === 'plan_confirm')
        ) {
          const schema = msg.interrupt?.schema || {}
          const steps = (response.payload?.steps || schema.steps || []).map((s, i) => ({
            id: String(s.id || i + 1),
            title: s.title || `步骤 ${i + 1}`,
            description: s.description || '',
            selected: s.selected !== false,
            status: s.selected === false ? 'skipped' : 'pending',
          }))
          next.plan = {
            title: msg.interrupt?.title || '执行计划',
            goal: schema.goal || '',
            steps,
            risks: schema.risks || [],
            assumptions: schema.assumptions || [],
            status: 'pending',
            completed_count: steps.filter(s => s.status === 'skipped').length,
            total_count: steps.length,
            current_step_id: null,
          }
        }

        // Outline 确认后立即展示快照（后续 SSE outline 会覆盖）
        if (
          response?.action === 'confirm'
          && reason === 'outline_confirm'
        ) {
          const schema = msg.interrupt?.schema || {}
          const chapters = response.payload?.chapters || schema.chapters || []
          next.events = [
            ...next.events,
            {
              type: 'outline',
              payload: {
                title: msg.interrupt?.title || '报告章节大纲',
                topic: schema.topic || '',
                chapters,
                action: 'confirm',
                selected_count: chapters.filter(c => c.selected !== false).length,
                total_count: chapters.length,
              },
            },
          ]
        }

        next.confirmResults = extractConfirmResultsFromEvents(next.events)
        return next
      }
      return msg
    }))

    await resume(runId, convId, response)
    loadConversations()
  }

  const handleNewChat = () => {
    setActiveConvId(null)
    setMessages([])
    loadedConvRef.current = null
    pendingRunRef.current = null
    pendingConvRef.current = null
    setReportDrawer({ open: false, runId: null, toolCallId: null })
  }

  const handleDelete = async (convId) => {
    try {
      await deleteConversation(convId)
      if (activeConvId === convId) {
        handleNewChat()
      }
      loadConversations()
    } catch (err) {
      console.error('删除会话失败:', err)
    }
  }

  const conversationTitle = useMemo(() => {
    if (!activeConvId) return ''
    const conv = conversations.find((c) => c._id === activeConvId)
    return conv?.title || ''
  }, [conversations, activeConvId])

  return (
    <div className="app-layout">
      <Sidebar
        conversations={conversations}
        activeId={activeConvId}
        onSelect={(id) => {
          if (id !== activeConvId) {
            // 断开当前 SSE 订阅，不取消后台任务
            detach()
            loadedConvRef.current = null
            pendingRunRef.current = null
            pendingConvRef.current = null
            setMessages([])
            setActiveConvId(id)
            setReportDrawer({ open: false, runId: null, toolCallId: null })
          }
        }}
        onNew={() => {
          detach()
          handleNewChat()
        }}
        onDelete={handleDelete}
      />
      <ChatWindow
        messages={messages}
        loading={loading}
        conversationTitle={conversationTitle}
        onSend={handleSend}
        onCancel={cancel}
        onHitlRespond={handleHitlRespond}
        onOpenReport={handleOpenReport}
      />
      <ReportStreamDrawer
        open={reportDrawer.open}
        report={activeReport}
        onClose={handleCloseReport}
      />
    </div>
  )
}
