import config from '../config.js'

const BASE = config.apiBase

/**
 * 获取会话列表
 */
export async function fetchConversations(start = 0, end = 20) {
  const res = await fetch(
    `${BASE}/conversations/list?start=${start}&end=${end}`,
    { headers: authHeaders() },
  )
  return res.json()
}

/**
 * 获取历史消息
 */
export async function fetchMessages(conversationId, start = 0, end = 10) {
  const res = await fetch(
    `${BASE}/conversations/${conversationId}/messages?start=${start}&end=${end}`,
    { headers: authHeaders() },
  )
  return res.json()
}

/**
 * 获取 Run Events（工具调用、工具结果等）
 */
export async function fetchRunEvents(conversationId, runIds) {
  if (!runIds || runIds.length === 0) {
    return { success: true, data: { items: [], total: 0 } }
  }
  const res = await fetch(
    `${BASE}/conversations/${conversationId}/runs/events?run_ids=${runIds.join(',')}`,
    { headers: authHeaders() },
  )
  return res.json()
}

/**
 * 获取会话中最近一个 interrupted run（用于刷新后恢复 HITL 面板）
 */
export async function fetchInterruptedRun(conversationId) {
  const res = await fetch(
    `${BASE}/conversations/${conversationId}/runs/interrupted`,
    { headers: authHeaders() },
  )
  return res.json()
}

/**
 * 获取会话中最近一个 running / interrupted run（刷新后恢复）
 */
export async function fetchActiveRun(conversationId) {
  const res = await fetch(
    `${BASE}/conversations/${conversationId}/runs/active`,
    { headers: authHeaders() },
  )
  return res.json()
}

/**
 * 显式取消 run
 */
export async function cancelRun(runId) {
  const res = await fetch(
    `${BASE}/chat/runs/${runId}/cancel`,
    { method: 'POST', headers: authHeaders() },
  )
  return res.json()
}

/**
 * 续订已有 run 的 SSE（after_seq 之后的事件）
 */
export function subscribeRunStream(runId, afterSeq, callbacks, signal) {
  const seq = afterSeq == null ? -1 : afterSeq
  const url = `${BASE}/chat/runs/${runId}/stream?after_seq=${seq}`
  return _consumeSSE(url, null, callbacks, { method: 'GET', signal })
}

/**
 * 删除会话
 */
export async function deleteConversation(conversationId) {
  const res = await fetch(
    `${BASE}/conversations/${conversationId}`,
    { method: 'DELETE', headers: authHeaders() },
  )
  return res.json()
}

/**
 * 更新会话标题
 */
export async function updateConversationTitle(conversationId, title) {
  const res = await fetch(
    `${BASE}/conversations/${conversationId}`,
    {
      method: 'PUT',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    },
  )
  return res.json()
}

/**
 * 统一发送消息/恢复对话（SSE 流式），返回 Promise
 * - 传 query → 新消息模式
 * - 传 approved / response → HITL 恢复模式
 */
export function sendMessageStream(query, conversationId, callbacks, options = {}, signal) {
  const body = {
    query,
    conversation_id: conversationId || '',
    deep_thinking: Boolean(options.deep_thinking),
    plan_mode: Boolean(options.plan_mode),
  }
  return _consumeSSE(`${BASE}/chat/stream`, body, callbacks, { signal })
}

/**
 * 恢复中断的对话（HITL 确认后继续）
 *
 * @param {string} conversationId
 * @param {{action: string, payload?: object}|boolean} responseOrApproved
 *   - 对象：结构化 HITL 响应 {action, payload}
 *   - 布尔：兼容旧协议 approved
 * @param {string|null} runId
 * @param {object} callbacks
 */
export function resumeMessageStream(conversationId, responseOrApproved, runId, callbacks, signal) {
  const body = { conversation_id: conversationId }
  if (typeof responseOrApproved === 'boolean') {
    body.approved = responseOrApproved
  } else if (responseOrApproved && typeof responseOrApproved === 'object') {
    body.response = responseOrApproved
  }
  if (runId) body.run_id = runId
  return _consumeSSE(`${BASE}/chat/stream`, body, callbacks, { signal })
}

/**
 * 通用 SSE 流消费函数
 *
 * 事件类型（后端新增 run_id / seq 字段）:
 * - run_started: { type, conversation_id, run_id, seq }
 * - content: { type, conversation_id, run_id, seq, content }
 * - reasoning: { type, conversation_id, run_id, seq, data: {delta|content} }
 * - tool_call: { type, conversation_id, run_id, seq, data }
 * - tool_response: { type, conversation_id, run_id, seq, data }
 * - interrupt: { type, conversation_id, run_id, seq, data }
 * - interrupted: { type, conversation_id, run_id, seq }
 * - artifact_start: { type, conversation_id, run_id, seq, data } — 报告开始生成
 * - artifact_delta: { type, conversation_id, run_id, seq, data } — 报告正文增量
 * - artifact: { type, conversation_id, run_id, seq, data } — 报告等独立产物终态
 * - plan: { type, conversation_id, run_id, seq, data } — Plan 进度快照
 * - approval: { type, conversation_id, run_id, seq, data } — HITL 确认结果
 * - outline: { type, conversation_id, run_id, seq, data } — 章节大纲确认快照
 * - usage: { type, conversation_id, run_id, seq, data } — token 用量
 * - error: { type, message, conversation_id, run_id, seq, usage? }
 * - done: { type, conversation_id, run_id, seq, message_id, usage? }
 *
 * Callbacks（所有回调可选，新增 onRunStarted）:
 * - onRunStarted(conversationId, runId)
 * - onToken(runId, content)
 * - onReasoning(runId, data)
 * - onToolCall(runId, data)
 * - onToolResponse(runId, data)
 * - onArtifactStart(runId, data)
 * - onArtifactDelta(runId, data)
 * - onArtifact(runId, data)
 * - onPlan(runId, data)
 * - onApproval(runId, data)
 * - onOutline(runId, data)
 * - onUsage(runId, data)
 * - onInterrupt(runId, data)
 * - onDone(runId, { conversationId, messageId, interrupted, usage })
 * - onError(runId, message, messageId, usage)
 */
function _consumeSSE(url, body, callbacks = {}, options = {}) {
  const {
    onRunStarted, onToken, onReasoning, onToolCall, onToolResponse,
    onArtifactStart, onArtifactDelta, onArtifact, onPlan, onApproval, onOutline,
    onUsage, onInterrupt, onDone, onError,
  } = callbacks

  const method = options.method || 'POST'
  const signal = options.signal
  const headers = { ...authHeaders() }
  const init = { method, headers, signal }
  if (method !== 'GET' && body != null) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }

  return fetch(url, init).then(async (response) => {
    if (!response.ok) {
      const err = await response.json().catch(() => ({}))
      throw new Error(err.message || `HTTP ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let lastRunId = ''
    let lastConversationId = ''
    let gotTerminal = false

    const markDone = (runId, payload) => {
      gotTerminal = true
      onDone?.(runId, payload)
    }

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed || !trimmed.startsWith('data: ')) continue

          const jsonStr = trimmed.slice(6)
          try {
            const event = JSON.parse(jsonStr)
            const runId = event.run_id || ''
            if (runId) lastRunId = runId
            if (event.conversation_id) lastConversationId = event.conversation_id
            switch (event.type) {
              case 'run_started':
                onRunStarted?.(event.conversation_id, event.run_id)
                break
              case 'content':
                onToken?.(runId, event.content)
                break
              case 'reasoning':
                onReasoning?.(runId, event.data || {})
                break
              case 'tool_call':
                onToolCall?.(runId, event.data)
                break
              case 'tool_response':
                onToolResponse?.(runId, event.data)
                break
              case 'artifact_start':
                onArtifactStart?.(runId, event.data)
                break
              case 'artifact_delta':
                onArtifactDelta?.(runId, event.data)
                break
              case 'artifact':
                onArtifact?.(runId, event.data)
                break
              case 'plan':
                onPlan?.(runId, event.data)
                break
              case 'approval':
                onApproval?.(runId, event.data)
                break
              case 'outline':
                onOutline?.(runId, event.data)
                break
              case 'usage':
                onUsage?.(runId, event.data || {})
                break
              case 'interrupt':
                onInterrupt?.(runId, event.data)
                break
              case 'interrupted':
                markDone(runId, {
                  conversationId: event.conversation_id,
                  messageId: null,
                  interrupted: true,
                  usage: event.usage || null,
                })
                break
              case 'done':
                markDone(runId, {
                  conversationId: event.conversation_id,
                  messageId: event.message_id || null,
                  interrupted: false,
                  usage: event.usage || null,
                })
                break
              case 'cancelled':
                markDone(runId, {
                  conversationId: event.conversation_id,
                  messageId: null,
                  interrupted: false,
                  cancelled: true,
                  usage: event.usage || null,
                })
                break
              case 'error':
                gotTerminal = true
                onError?.(runId, event.message, event.message_id || null, event.usage || null)
                break
            }
          } catch {
            // 忽略解析失败的行
          }
        }
      }

      // 流关闭但未收到 done/error：补一次 onDone，避免 loading 永久挂起
      if (!gotTerminal && lastRunId) {
        markDone(lastRunId, {
          conversationId: lastConversationId || null,
          messageId: null,
          interrupted: false,
          usage: null,
        })
      }
    } catch (err) {
      if (err?.name === 'AbortError') return
      throw err
    }
  })
}

function authHeaders() {
  return { Authorization: 'Bearer guest' }
}
