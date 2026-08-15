/**
 * 统一人机协作交互面板
 *
 * 根据 interrupt.reason / schema.type 渲染不同变体：
 * - tool_approval: 工具调用批准/拒绝
 * - outline_confirm: 章节勾选、编辑、确认 / 要求修改 / 取消
 * - plan_confirm: 执行计划勾选、编辑、确认 / 要求修改 / 取消
 */
import { useMemo, useState } from 'react'
import { Check, X, Plus, Trash2, Pencil, AlertTriangle, ListTodo } from 'lucide-react'
import './InteractivePanel.css'

export default function InteractivePanel({ interrupt, onRespond }) {
  const normalized = useMemo(() => normalizeInterrupt(interrupt), [interrupt])
  if (!normalized) return null

  const reason = normalized.reason
  if (reason === 'plan_confirm' || normalized.schema?.type === 'plan_confirm') {
    return (
      <PlanConfirmPanel
        interrupt={normalized}
        onRespond={onRespond}
      />
    )
  }
  if (reason === 'outline_confirm' || normalized.schema?.type === 'outline_confirm') {
    return (
      <OutlineConfirmPanel
        interrupt={normalized}
        onRespond={onRespond}
      />
    )
  }

  return (
    <ToolApprovalPanel
      interrupt={normalized}
      onRespond={onRespond}
    />
  )
}

function ToolApprovalPanel({ interrupt, onRespond }) {
  const tools = interrupt.tool_calls || []
  if (!tools.length) return null

  return (
    <div className="interactive-panel interactive-panel--tool">
      <div className="interactive-header">
        <AlertTriangle size={16} className="interactive-warn-icon" />
        <div>
          <div className="interactive-title">{interrupt.title || '工具调用确认'}</div>
          <div className="interactive-subtitle">Agent 请求执行以下工具</div>
        </div>
      </div>
      <div className="interactive-body">
        {tools.map((tool, i) => (
          <div key={tool.id || i} className="tool-item">
            <div className="tool-name">{tool.name}</div>
            {tool.args && Object.keys(tool.args).length > 0 && (
              <div className="tool-args">
                {Object.entries(tool.args).map(([k, v]) => (
                  <span key={k} className="tool-arg">
                    <span className="arg-key">{k}</span>={' '}
                    <span className="arg-value">{String(v)}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="interactive-actions">
        <button
          className="interactive-btn approve"
          onClick={() => onRespond?.({ action: 'approve' })}
        >
          <Check size={14} />
          批准
        </button>
        <button
          className="interactive-btn deny"
          onClick={() => onRespond?.({ action: 'deny' })}
        >
          <X size={14} />
          拒绝
        </button>
      </div>
    </div>
  )
}

function OutlineConfirmPanel({ interrupt, onRespond }) {
  const initialChapters = interrupt.schema?.chapters || []
  const [chapters, setChapters] = useState(() =>
    initialChapters.map((ch, i) => ({
      id: String(ch.id || i + 1),
      title: ch.title || `章节 ${i + 1}`,
      description: ch.description || '',
      selected: ch.selected !== false,
    })),
  )
  const [feedback, setFeedback] = useState('')
  const [showFeedback, setShowFeedback] = useState(false)

  const topic = interrupt.schema?.topic || ''

  const updateChapter = (index, patch) => {
    setChapters(prev => prev.map((ch, i) => (i === index ? { ...ch, ...patch } : ch)))
  }

  const removeChapter = (index) => {
    setChapters(prev => prev.filter((_, i) => i !== index))
  }

  const addChapter = () => {
    setChapters(prev => [
      ...prev,
      {
        id: String(Date.now()),
        title: '新章节',
        description: '',
        selected: true,
      },
    ])
  }

  const handleConfirm = () => {
    const selected = chapters.filter(ch => ch.selected)
    if (selected.length === 0) {
      alert('请至少选择一个章节')
      return
    }
    onRespond?.({
      action: 'confirm',
      payload: { chapters },
    })
  }

  const handleRevise = () => {
    if (!showFeedback) {
      setShowFeedback(true)
      return
    }
    if (!feedback.trim()) {
      alert('请填写修改意见')
      return
    }
    onRespond?.({
      action: 'revise',
      payload: { feedback: feedback.trim(), chapters },
    })
  }

  const handleCancel = () => {
    onRespond?.({ action: 'cancel', payload: null })
  }

  return (
    <div className="interactive-panel interactive-panel--outline">
      <div className="interactive-header">
        <Pencil size={16} className="interactive-info-icon" />
        <div>
          <div className="interactive-title">{interrupt.title || '确认报告章节大纲'}</div>
          <div className="interactive-subtitle">
            {topic ? `主题：${topic} · ` : ''}
            可勾选、编辑章节后确认；也可要求修改或取消
          </div>
        </div>
      </div>

      <div className="interactive-body outline-list">
        {chapters.map((ch, index) => (
          <div key={ch.id} className={`outline-item ${ch.selected ? '' : 'outline-item--off'}`}>
            <label className="outline-check">
              <input
                type="checkbox"
                checked={ch.selected}
                onChange={(e) => updateChapter(index, { selected: e.target.checked })}
              />
            </label>
            <div className="outline-fields">
              <input
                className="outline-title-input"
                value={ch.title}
                onChange={(e) => updateChapter(index, { title: e.target.value })}
                placeholder="章节标题"
              />
              <input
                className="outline-desc-input"
                value={ch.description}
                onChange={(e) => updateChapter(index, { description: e.target.value })}
                placeholder="章节说明（可选）"
              />
            </div>
            <button
              type="button"
              className="outline-remove"
              title="删除章节"
              onClick={() => removeChapter(index)}
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        <button type="button" className="outline-add" onClick={addChapter}>
          <Plus size={14} />
          添加章节
        </button>
      </div>

      {showFeedback && (
        <div className="outline-feedback">
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="请说明希望如何调整章节目录…"
            rows={3}
          />
        </div>
      )}

      <div className="interactive-actions">
        <button className="interactive-btn approve" onClick={handleConfirm}>
          <Check size={14} />
          确认并开始撰写
        </button>
        <button className="interactive-btn revise" onClick={handleRevise}>
          <Pencil size={14} />
          {showFeedback ? '提交修改意见' : '要求修改'}
        </button>
        <button className="interactive-btn deny" onClick={handleCancel}>
          <X size={14} />
          取消
        </button>
      </div>
    </div>
  )
}

function PlanConfirmPanel({ interrupt, onRespond }) {
  const initialSteps = interrupt.schema?.steps || []
  const [steps, setSteps] = useState(() =>
    initialSteps.map((step, i) => ({
      id: String(step.id || i + 1),
      title: step.title || `步骤 ${i + 1}`,
      description: step.description || '',
      selected: step.selected !== false,
    })),
  )
  const [feedback, setFeedback] = useState('')
  const [showFeedback, setShowFeedback] = useState(false)

  const goal = interrupt.schema?.goal || ''
  const risks = interrupt.schema?.risks || []
  const assumptions = interrupt.schema?.assumptions || []

  const updateStep = (index, patch) => {
    setSteps(prev => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)))
  }

  const removeStep = (index) => {
    setSteps(prev => prev.filter((_, i) => i !== index))
  }

  const addStep = () => {
    setSteps(prev => [
      ...prev,
      {
        id: String(Date.now()),
        title: '新步骤',
        description: '',
        selected: true,
      },
    ])
  }

  const handleConfirm = () => {
    const selected = steps.filter(s => s.selected)
    if (selected.length === 0) {
      alert('请至少选择一个步骤')
      return
    }
    onRespond?.({
      action: 'confirm',
      payload: { steps },
    })
  }

  const handleRevise = () => {
    if (!showFeedback) {
      setShowFeedback(true)
      return
    }
    if (!feedback.trim()) {
      alert('请填写修改意见')
      return
    }
    onRespond?.({
      action: 'revise',
      payload: { feedback: feedback.trim(), steps },
    })
  }

  const handleCancel = () => {
    onRespond?.({ action: 'cancel', payload: null })
  }

  return (
    <div className="interactive-panel interactive-panel--plan">
      <div className="interactive-header">
        <ListTodo size={16} className="interactive-plan-icon" />
        <div>
          <div className="interactive-title">{interrupt.title || '确认执行计划'}</div>
          <div className="interactive-subtitle">
            {goal ? `目标：${goal}` : '可勾选、编辑步骤后确认；也可要求修改或取消'}
          </div>
        </div>
      </div>

      {(risks.length > 0 || assumptions.length > 0) && (
        <div className="plan-meta">
          {assumptions.length > 0 && (
            <div className="plan-meta-block">
              <div className="plan-meta-label">假设</div>
              <ul>
                {assumptions.map((a, i) => (
                  <li key={`a-${i}`}>{a}</li>
                ))}
              </ul>
            </div>
          )}
          {risks.length > 0 && (
            <div className="plan-meta-block">
              <div className="plan-meta-label">风险</div>
              <ul>
                {risks.map((r, i) => (
                  <li key={`r-${i}`}>{r}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <div className="interactive-body outline-list">
        {steps.map((step, index) => (
          <div key={step.id} className={`outline-item ${step.selected ? '' : 'outline-item--off'}`}>
            <label className="outline-check">
              <input
                type="checkbox"
                checked={step.selected}
                onChange={(e) => updateStep(index, { selected: e.target.checked })}
              />
            </label>
            <div className="outline-fields">
              <input
                className="outline-title-input"
                value={step.title}
                onChange={(e) => updateStep(index, { title: e.target.value })}
                placeholder="步骤标题"
              />
              <input
                className="outline-desc-input"
                value={step.description}
                onChange={(e) => updateStep(index, { description: e.target.value })}
                placeholder="步骤说明（可选）"
              />
            </div>
            <button
              type="button"
              className="outline-remove"
              title="删除步骤"
              onClick={() => removeStep(index)}
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        <button type="button" className="outline-add" onClick={addStep}>
          <Plus size={14} />
          添加步骤
        </button>
      </div>

      {showFeedback && (
        <div className="outline-feedback">
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="请说明希望如何调整执行计划…"
            rows={3}
          />
        </div>
      )}

      <div className="interactive-actions">
        <button className="interactive-btn approve" onClick={handleConfirm}>
          <Check size={14} />
          确认并开始执行
        </button>
        <button className="interactive-btn revise" onClick={handleRevise}>
          <Pencil size={14} />
          {showFeedback ? '提交修改意见' : '要求修改'}
        </button>
        <button className="interactive-btn deny" onClick={handleCancel}>
          <X size={14} />
          取消
        </button>
      </div>
    </div>
  )
}

/**
 * 归一化 interrupt 数据：兼容旧版「纯 tool 数组」与新版结构化对象
 */
export function normalizeInterrupt(data) {
  if (!data) return null
  if (Array.isArray(data)) {
    if (data.length === 0) return null
    return {
      reason: 'tool_approval',
      title: '工具调用确认',
      schema: { type: 'tool_approval' },
      actions: [
        { id: 'approve', label: '批准' },
        { id: 'deny', label: '拒绝' },
      ],
      tool_calls: data,
    }
  }
  if (typeof data === 'object') {
    const schemaType = data.schema?.type
    let reason = data.reason
    if (!reason) {
      if (schemaType === 'outline_confirm') reason = 'outline_confirm'
      else if (schemaType === 'plan_confirm') reason = 'plan_confirm'
      else reason = 'tool_approval'
    }
    return {
      reason,
      title: data.title || '',
      schema: data.schema || {},
      actions: data.actions || [],
      tool_calls: data.tool_calls || [],
    }
  }
  return null
}
