/**
 * HITL 确认结果回放卡片
 *
 * 展示用户对章节大纲 / 计划清单的确认结果（含修订、取消），便于历史回溯。
 * plan_confirm + confirm 由 PlanProgressCard 承接，此处跳过以免重复。
 */
import { useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronRight,
  ListTodo,
  Pencil,
  X,
} from 'lucide-react'
import './ConfirmResultCard.css'

const ACTION_META = {
  confirm: { label: '已确认', Icon: Check },
  revise: { label: '要求修改', Icon: Pencil },
  cancel: { label: '已取消', Icon: X },
  approve: { label: '已批准', Icon: Check },
  deny: { label: '已拒绝', Icon: X },
}

export default function ConfirmResultCard({ result }) {
  if (!result) return null

  const items = Array.isArray(result.items) ? result.items : []
  const action = result.action || 'confirm'
  const meta = ACTION_META[action] || ACTION_META.confirm
  const ActionIcon = meta.Icon
  const isOutline = result.kind === 'outline'
  const TitleIcon = isOutline ? Pencil : ListTodo
  const selectedCount = items.filter((it) => it.selected !== false).length
  const [expanded, setExpanded] = useState(action !== 'confirm')

  const kindLabel = isOutline ? '章节大纲' : '执行计划'
  const countLabel = items.length
    ? `${selectedCount}/${items.length}`
    : null
  const summaryParts = [meta.label]
  if (countLabel) summaryParts.push(countLabel)
  if (result.feedback) summaryParts.push('含修改意见')

  return (
    <div
      className={`confirm-result confirm-result--${result.kind || 'generic'} confirm-result--${action}${
        expanded ? ' confirm-result--open' : ''
      }`}
    >
      <button
        type="button"
        className="confirm-result-toggle"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="confirm-result-toggle-icon">
          <TitleIcon size={14} />
        </span>
        <span className="confirm-result-toggle-label">
          <span className="confirm-result-toggle-title">
            {result.title || kindLabel}
          </span>
          <span className="confirm-result-toggle-meta">
            {summaryParts.join(' · ')}
          </span>
        </span>
        <span className="confirm-result-badge">
          <ActionIcon size={12} />
          {meta.label}
        </span>
        <span className="confirm-result-toggle-chevron">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>

      {expanded && (
        <div className="confirm-result-body">
          {result.topic ? (
            <div className="confirm-result-meta-line">主题：{result.topic}</div>
          ) : null}
          {result.goal ? (
            <div className="confirm-result-meta-line">目标：{result.goal}</div>
          ) : null}
          {result.feedback ? (
            <div className="confirm-result-feedback">
              修改意见：{result.feedback}
            </div>
          ) : null}

          {items.length > 0 && (
            <ol className="confirm-result-items">
              {items.map((item, index) => {
                const selected = item.selected !== false
                return (
                  <li
                    key={item.id || index}
                    className={`confirm-result-item${
                      selected ? '' : ' confirm-result-item--off'
                    }`}
                  >
                    <span className="confirm-result-item-mark">
                      {selected ? '✓' : '–'}
                    </span>
                    <div className="confirm-result-item-body">
                      <div className="confirm-result-item-title">
                        <span className="confirm-result-item-index">
                          {index + 1}.
                        </span>
                        {item.title || `${isOutline ? '章节' : '步骤'} ${index + 1}`}
                      </div>
                      {item.description ? (
                        <div className="confirm-result-item-desc">
                          {item.description}
                        </div>
                      ) : null}
                    </div>
                  </li>
                )
              })}
            </ol>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * 从 run events 提取确认结果列表（按 seq 顺序）
 *
 * - outline 确认：优先 type=outline 快照；无快照时回退 enrichment approval
 * - plan_confirm + confirm：跳过（由 PlanProgressCard 展示）
 * - revise / cancel：一律从 approval 展示
 * - 兼容旧 approval（无 reason）：向前匹配最近一次 interrupt
 */
export function extractConfirmResultsFromEvents(events) {
  if (!Array.isArray(events) || events.length === 0) return []

  const hasOutlineSnapshot = events.some(
    (ev) => ev?.type === 'outline' && ev.payload && typeof ev.payload === 'object',
  )
  const results = []
  let lastInterrupt = null

  for (const ev of events) {
    if (ev?.type === 'interrupt' && ev.payload && typeof ev.payload === 'object') {
      lastInterrupt = ev.payload
      continue
    }

    if (ev?.type === 'outline' && ev.payload && typeof ev.payload === 'object') {
      const outline = ev.payload
      results.push({
        key: `outline_${ev.seq ?? results.length}`,
        kind: 'outline',
        action: outline.action || 'confirm',
        title: outline.title || '报告章节大纲',
        topic: outline.topic || '',
        items: Array.isArray(outline.chapters) ? outline.chapters : [],
        feedback: '',
        seq: ev.seq ?? 0,
      })
      continue
    }

    if (ev?.type !== 'approval' || !ev.payload || typeof ev.payload !== 'object') {
      continue
    }

    const payload = ev.payload
    const reason = payload.reason
      || payload.schema?.type
      || lastInterrupt?.reason
      || lastInterrupt?.schema?.type
      || ''
    const action = payload.action || ''
    if (!reason || !action) continue

    // plan 确认后由 PlanProgressCard 展示执行进度，避免重复卡片
    if (reason === 'plan_confirm' && action === 'confirm') continue

    // 已有 outline 快照时，跳过 outline confirm 的 approval，避免重复
    if (reason === 'outline_confirm' && action === 'confirm' && hasOutlineSnapshot) {
      continue
    }

    const userPayload = payload.payload && typeof payload.payload === 'object'
      ? payload.payload
      : {}
    const title = payload.title
      || lastInterrupt?.title
      || ''
    const topic = payload.topic
      || lastInterrupt?.schema?.topic
      || ''
    const goal = payload.goal
      || lastInterrupt?.schema?.goal
      || ''

    if (reason === 'outline_confirm') {
      const chapters = Array.isArray(userPayload.chapters)
        ? userPayload.chapters
        : []
      results.push({
        key: `approval_${ev.seq ?? results.length}`,
        kind: 'outline',
        action,
        title: title || '报告章节大纲',
        topic,
        items: chapters,
        feedback: userPayload.feedback || '',
        seq: ev.seq ?? 0,
      })
      continue
    }

    if (reason === 'plan_confirm') {
      const steps = Array.isArray(userPayload.steps) ? userPayload.steps : []
      results.push({
        key: `approval_${ev.seq ?? results.length}`,
        kind: 'plan',
        action,
        title: title || '执行计划',
        goal,
        items: steps,
        feedback: userPayload.feedback || '',
        seq: ev.seq ?? 0,
      })
    }
  }

  return results
}
