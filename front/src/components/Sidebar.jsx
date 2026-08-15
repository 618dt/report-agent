import { useMemo, useState } from 'react'
import { MessageSquare, Plus, Trash2, Menu, X } from 'lucide-react'
import './Sidebar.css'

function formatSessionTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function sessionTimestamp(conversation) {
  return conversation?.update_time || conversation?.create_time || 0
}

function toDayKey(ts) {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return 'unknown'
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function formatDayLabel(dayKey) {
  if (!dayKey || dayKey === 'unknown') return '未知日期'
  const [y, m, d] = dayKey.split('-').map(Number)
  const date = new Date(y, m - 1, d)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const target = new Date(date)
  target.setHours(0, 0, 0, 0)
  const diffDays = Math.round((today - target) / 86400000)
  if (diffDays === 0) return '今天'
  if (diffDays === 1) return '昨天'
  if (date.getFullYear() === today.getFullYear()) {
    return `${m}月${d}日`
  }
  return `${y}年${m}月${d}日`
}

/**
 * 按「最早 → 最近」插值浅色底：冷灰蓝 → 雾绿（贴合品牌绿，低饱和、高明度）
 * t=0 最早，t=1 最近
 */
function dayGroupColor(t) {
  const clamped = Math.min(1, Math.max(0, t))
  const hue = 208 - clamped * 52 // 208(冷蓝) → 156(薄荷绿)
  const sat = 16 + clamped * 14 // 16% → 30%
  const light = 96.5 - clamped * 1.8 // 96.5% → 94.7%
  return `hsl(${hue} ${sat}% ${light}%)`
}

function groupConversationsByDay(conversations) {
  const map = new Map()
  for (const c of conversations) {
    const ts = sessionTimestamp(c)
    const key = toDayKey(ts)
    if (!map.has(key)) {
      map.set(key, {
        key,
        label: formatDayLabel(key),
        sortTs: key === 'unknown' ? 0 : new Date(`${key}T00:00:00`).getTime(),
        items: [],
      })
    }
    map.get(key).items.push(c)
  }

  // 列表整体按最近在上；组内保持接口原有顺序
  const groups = [...map.values()].sort((a, b) => b.sortTs - a.sortTs)
  if (groups.length === 0) return []

  const oldest = Math.min(...groups.map((g) => g.sortTs))
  const newest = Math.max(...groups.map((g) => g.sortTs))
  const span = Math.max(newest - oldest, 1)

  return groups.map((g) => ({
    ...g,
    // 仅一天时用「最近」端色，避免整列都落在最早冷色
    color: dayGroupColor(
      groups.length === 1 ? 1 : (g.sortTs - oldest) / span,
    ),
  }))
}

export default function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}) {
  const [open, setOpen] = useState(false)
  const [tooltip, setTooltip] = useState(null)
  const [pendingDelete, setPendingDelete] = useState(null)

  const dayGroups = useMemo(
    () => groupConversationsByDay(conversations),
    [conversations],
  )

  const handleSelect = (id) => {
    onSelect(id)
    setOpen(false)
    setTooltip(null)
  }

  const handleNew = () => {
    onNew()
    setOpen(false)
    setTooltip(null)
  }

  const showTooltip = (e, conversation) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const title = conversation.title || '新建对话'
    const time = formatSessionTime(sessionTimestamp(conversation))
    const gap = 8
    const maxWidth = 280
    const viewportPad = 8
    let left = rect.right + gap
    const top = rect.top + rect.height / 2
    let placeLeft = false

    if (left + maxWidth > window.innerWidth - viewportPad) {
      left = Math.max(viewportPad, rect.left - gap)
      placeLeft = true
    }

    setTooltip({
      id: conversation._id,
      title,
      time,
      top,
      left,
      placeLeft,
    })
  }

  const hideTooltip = () => setTooltip(null)

  const requestDelete = (conversation) => {
    hideTooltip()
    setPendingDelete({
      id: conversation._id,
      title: conversation.title || '新建对话',
    })
  }

  const cancelDelete = () => setPendingDelete(null)

  const confirmDelete = () => {
    if (!pendingDelete) return
    const id = pendingDelete.id
    setPendingDelete(null)
    onDelete(id)
  }

  return (
    <>
      {!open && (
        <button
          className="sidebar-toggle"
          onClick={() => setOpen(true)}
          aria-label="打开侧边栏"
        >
          <Menu size={20} />
        </button>
      )}

      {open && <div className="sidebar-overlay" onClick={() => setOpen(false)} />}

      <aside className={`sidebar ${open ? 'sidebar--open' : ''}`}>
        <div className="sidebar-header">
          <div className="sidebar-logo">
            <div className="logo-dot" />
            <span>Report Agent</span>
          </div>
          <button
            className="sidebar-close"
            onClick={() => setOpen(false)}
            aria-label="关闭侧边栏"
          >
            <X size={18} />
          </button>
        </div>

        <button className="btn-new-chat" onClick={handleNew}>
          <Plus size={16} />
          <span>新对话</span>
        </button>

        <div className="sidebar-list">
          {dayGroups.map((group) => (
            <section
              key={group.key}
              className="sidebar-day-group"
              style={{ '--day-tint': group.color }}
              aria-label={group.label}
            >
              <div className="sidebar-day-label">{group.label}</div>
              {group.items.map((c) => {
                const itemTitle = c.title || '新建对话'
                return (
                  <div
                    key={c._id}
                    className={`sidebar-item ${activeId === c._id ? 'sidebar-item--active' : ''}`}
                    onClick={() => handleSelect(c._id)}
                    onMouseEnter={(e) => showTooltip(e, c)}
                    onMouseLeave={hideTooltip}
                    onFocus={(e) => showTooltip(e, c)}
                    onBlur={hideTooltip}
                  >
                    <MessageSquare size={14} className="sidebar-item-icon" />
                    <span className="sidebar-item-title">{itemTitle}</span>
                    <button
                      className="sidebar-item-del"
                      onClick={(e) => {
                        e.stopPropagation()
                        requestDelete(c)
                      }}
                      title="删除"
                      aria-label="删除会话"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                )
              })}
            </section>
          ))}
          {conversations.length === 0 && (
            <div className="sidebar-empty">暂无对话</div>
          )}
        </div>
      </aside>

      {tooltip && !pendingDelete && (
        <div
          className={`sidebar-item-tooltip${tooltip.placeLeft ? ' sidebar-item-tooltip--left' : ''}`}
          style={{ top: tooltip.top, left: tooltip.left }}
          role="tooltip"
        >
          <div className="sidebar-item-tooltip-title">{tooltip.title}</div>
          {tooltip.time && (
            <div className="sidebar-item-tooltip-time">会话时间：{tooltip.time}</div>
          )}
        </div>
      )}

      {pendingDelete && (
        <div className="sidebar-confirm-mask" onClick={cancelDelete}>
          <div
            className="sidebar-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="sidebar-confirm-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div id="sidebar-confirm-title" className="sidebar-confirm-title">
              删除会话
            </div>
            <p className="sidebar-confirm-desc">
              确定删除「{pendingDelete.title}」吗？删除后不可恢复。
            </p>
            <div className="sidebar-confirm-actions">
              <button
                type="button"
                className="sidebar-confirm-btn sidebar-confirm-btn--cancel"
                onClick={cancelDelete}
              >
                取消
              </button>
              <button
                type="button"
                className="sidebar-confirm-btn sidebar-confirm-btn--danger"
                onClick={confirmDelete}
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
