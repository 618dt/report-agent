/**
 * Plan 执行进度可视化（类 Cursor）
 * 展示完整步骤列表：completed / running / pending / skipped
 * 执行中自动展开；全部完成后自动收起，可手动折叠。
 */
import { useEffect, useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  ListTodo,
  Loader,
  Minus,
} from 'lucide-react'
import './PlanProgressCard.css'

const STATUS_META = {
  completed: { label: '已完成', Icon: Check },
  running: { label: '进行中', Icon: Loader },
  pending: { label: '待执行', Icon: Circle },
  skipped: { label: '已跳过', Icon: Minus },
}

export default function PlanProgressCard({ plan }) {
  if (!plan || !Array.isArray(plan.steps) || plan.steps.length === 0) {
    return null
  }

  const steps = plan.steps
  const total = plan.total_count ?? steps.length
  const completed = plan.completed_count ?? steps.filter(
    (s) => s.status === 'completed' || s.status === 'skipped',
  ).length
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0
  const hasRunning = steps.some((s) => s.status === 'running')
  const done = plan.status === 'completed' || (total > 0 && completed >= total)
  const isLive = !done && (hasRunning || plan.status === 'running' || plan.status === 'pending')

  const [expanded, setExpanded] = useState(isLive)

  useEffect(() => {
    // 执行中自动展开；全部完成后自动收起
    setExpanded(isLive)
  }, [isLive])

  const summary = done
    ? `计划已完成 · ${completed}/${total}`
    : hasRunning
      ? `计划执行中 · ${completed}/${total}`
      : `执行计划 · ${completed}/${total}`

  return (
    <div
      className={`plan-progress${done ? ' plan-progress--done' : ''}${
        expanded ? ' plan-progress--open' : ''
      }`}
    >
      <button
        type="button"
        className="plan-progress-toggle"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="plan-progress-toggle-icon">
          {done ? (
            <Check size={14} />
          ) : hasRunning ? (
            <Loader size={14} className="spin" />
          ) : (
            <ListTodo size={14} />
          )}
        </span>
        <span className="plan-progress-toggle-label">
          <span className="plan-progress-toggle-title">
            {plan.title || '执行计划'}
          </span>
          <span className="plan-progress-toggle-meta">{summary}</span>
        </span>
        <span className="plan-progress-count">{completed}/{total}</span>
        <span className="plan-progress-toggle-chevron">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>

      {expanded && (
        <div className="plan-progress-body">
          {plan.goal ? (
            <div className="plan-progress-goal">{plan.goal}</div>
          ) : null}

          <div className="plan-progress-bar" aria-hidden>
            <div
              className="plan-progress-bar-fill"
              style={{ width: `${pct}%` }}
            />
          </div>

          <ol className="plan-progress-steps">
            {steps.map((step, index) => {
              const status = step.status || 'pending'
              const meta = STATUS_META[status] || STATUS_META.pending
              const Icon = meta.Icon
              return (
                <li
                  key={step.id || index}
                  className={`plan-progress-step plan-progress-step--${status}`}
                >
                  <span className="plan-progress-step-icon" title={meta.label}>
                    <Icon
                      size={14}
                      className={status === 'running' ? 'spin' : undefined}
                    />
                  </span>
                  <div className="plan-progress-step-body">
                    <div className="plan-progress-step-title">
                      <span className="plan-progress-step-index">
                        {index + 1}.
                      </span>
                      {step.title || `步骤 ${index + 1}`}
                    </div>
                    {step.description ? (
                      <div className="plan-progress-step-desc">
                        {step.description}
                      </div>
                    ) : null}
                    {step.note ? (
                      <div className="plan-progress-step-note">{step.note}</div>
                    ) : null}
                  </div>
                  <span className="plan-progress-step-status">{meta.label}</span>
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </div>
  )
}

/** 从 run events 中提取最新 plan 快照 */
export function extractPlanFromEvents(events) {
  if (!Array.isArray(events)) return null
  let latest = null
  for (const ev of events) {
    if (ev?.type === 'plan' && ev.payload && typeof ev.payload === 'object') {
      latest = ev.payload
    }
  }
  return latest
}
