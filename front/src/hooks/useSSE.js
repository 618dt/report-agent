import { useState, useRef, useCallback } from 'react'
import {
  sendMessageStream,
  resumeMessageStream,
  subscribeRunStream,
  cancelRun,
} from '../api/index.js'

/**
 * SSE 对话 Hook
 *
 * @param {object|React.MutableRefObject} callbacksRefOrObj
 *   App 侧通常传入 useRef({...})，此处自动解包 .current
 */
export function useSSE(callbacksRefOrObj = {}) {
  const [loading, setLoading] = useState(false)
  const abortRef = useRef(null)
  const activeRunIdRef = useRef(null)

  const getCallbacks = useCallback(() => (
    callbacksRefOrObj?.current ?? callbacksRefOrObj ?? {}
  ), [callbacksRefOrObj])

  const bindStreamCallbacks = useCallback((runIdOverride) => {
    const callbacks = getCallbacks()
    return {
      onToken(runId, content) {
        callbacks.onContent?.(runIdOverride || runId, content)
      },
      onReasoning(runId, data) {
        callbacks.onReasoning?.(runIdOverride || runId, data)
      },
      onToolCall(runId, data) {
        callbacks.onToolCall?.(runIdOverride || runId, data)
      },
      onToolResponse(runId, data) {
        callbacks.onToolResponse?.(runIdOverride || runId, data)
      },
      onArtifactStart(runId, data) {
        callbacks.onArtifactStart?.(runIdOverride || runId, data)
      },
      onArtifactDelta(runId, data) {
        callbacks.onArtifactDelta?.(runIdOverride || runId, data)
      },
      onArtifact(runId, data) {
        callbacks.onArtifact?.(runIdOverride || runId, data)
      },
      onPlan(runId, data) {
        callbacks.onPlan?.(runIdOverride || runId, data)
      },
      onApproval(runId, data) {
        callbacks.onApproval?.(runIdOverride || runId, data)
      },
      onOutline(runId, data) {
        callbacks.onOutline?.(runIdOverride || runId, data)
      },
      onUsage(runId, data) {
        callbacks.onUsage?.(runIdOverride || runId, data)
      },
      onInterrupt(runId, data) {
        setLoading(false)
        callbacks.onInterrupt?.(runIdOverride || runId, data)
      },
      onDone(runId, { conversationId, messageId, interrupted, usage, cancelled }) {
        setLoading(false)
        activeRunIdRef.current = null
        abortRef.current = null
        callbacks.onDone?.(runIdOverride || runId, {
          messageId, interrupted, conversationId, usage, cancelled,
        })
      },
      onError(runId, message, messageId, usage) {
        setLoading(false)
        activeRunIdRef.current = null
        abortRef.current = null
        callbacks.onError?.(runIdOverride || runId, message, messageId, usage)
      },
    }
  }, [getCallbacks])

  const send = useCallback(async (query, conversationId, options = {}) => {
    const callbacks = getCallbacks()
    setLoading(true)
    if (abortRef.current) {
      abortRef.current.abort()
    }
    const controller = new AbortController()
    abortRef.current = controller

    let currentRunId = null
    let resolvedConvId = conversationId || ''

    try {
      await sendMessageStream(query, conversationId || '', {
        onRunStarted(convId, runId) {
          currentRunId = runId
          activeRunIdRef.current = runId
          resolvedConvId = convId
          callbacks.onRunStarted?.(convId, runId)
        },
        ...bindStreamCallbacks(null),
        onDone(runId, { conversationId: convId, messageId, interrupted, usage, cancelled }) {
          resolvedConvId = convId || resolvedConvId
          setLoading(false)
          activeRunIdRef.current = null
          abortRef.current = null
          callbacks.onDone?.(runId, { messageId, interrupted, usage, cancelled })
        },
      }, options, controller.signal)
    } catch (err) {
      if (err?.name !== 'AbortError') {
        setLoading(false)
        callbacks.onError?.(currentRunId, `连接失败: ${err.message}`)
      }
    }

    return { runId: currentRunId, conversationId: resolvedConvId }
  }, [getCallbacks, bindStreamCallbacks])

  const resume = useCallback(async (runId, conversationId, responseOrApproved) => {
    const callbacks = getCallbacks()
    setLoading(true)
    callbacks.onContent?.(runId, '')
    activeRunIdRef.current = runId

    if (abortRef.current) {
      abortRef.current.abort()
    }
    const controller = new AbortController()
    abortRef.current = controller

    let wasInterrupted = false

    try {
      await resumeMessageStream(conversationId, responseOrApproved, runId, {
        onRunStarted() {},
        ...bindStreamCallbacks(runId),
        onDone(_runId, { messageId, interrupted, usage, cancelled }) {
          wasInterrupted = interrupted
          setLoading(false)
          activeRunIdRef.current = null
          abortRef.current = null
          callbacks.onDone?.(runId, { messageId, interrupted, usage, cancelled })
        },
      }, controller.signal)
    } catch (err) {
      if (err?.name !== 'AbortError') {
        setLoading(false)
        callbacks.onError?.(runId, `连接失败: ${err.message}`)
      }
    }

    return { interrupted: wasInterrupted }
  }, [getCallbacks, bindStreamCallbacks])

  const subscribe = useCallback(async (runId, afterSeq = -1) => {
    if (!runId) return
    const callbacks = getCallbacks()
    setLoading(true)
    activeRunIdRef.current = runId

    if (abortRef.current) {
      abortRef.current.abort()
    }
    const controller = new AbortController()
    abortRef.current = controller

    try {
      await subscribeRunStream(runId, afterSeq, {
        onRunStarted(convId, rid) {
          callbacks.onRunStarted?.(convId, rid)
        },
        ...bindStreamCallbacks(runId),
      }, controller.signal)
    } catch (err) {
      if (err?.name !== 'AbortError') {
        setLoading(false)
        callbacks.onError?.(runId, `续订失败: ${err.message}`)
      }
    }
  }, [getCallbacks, bindStreamCallbacks])

  const cancel = useCallback(async () => {
    const runId = activeRunIdRef.current
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setLoading(false)
    if (runId) {
      try {
        await cancelRun(runId)
      } catch (err) {
        console.error('取消 run 失败:', err)
      }
      activeRunIdRef.current = null
    }
  }, [])

  /** 仅断开前端 SSE，不取消后台 run（切换会话时用） */
  const detach = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    activeRunIdRef.current = null
    setLoading(false)
  }, [])

  return { loading, send, resume, subscribe, cancel, detach }
}
