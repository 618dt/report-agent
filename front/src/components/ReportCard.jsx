/**
 * 报告卡片：主聊天中的生成中占位 / 完成后的跳转块
 * 正文阅读与复制在 ReportStreamDrawer 中完成
 */
import { FileText, Loader2, ChevronRight } from 'lucide-react'
import './ReportCard.css'

function resolveWordCount(artifact) {
  if (typeof artifact?.word_count === 'number' && artifact.word_count >= 0) {
    return artifact.word_count
  }
  const md = artifact?.markdown
  if (!md) return null
  const matches = md.match(/[\u4e00-\u9fff]/g)
  return matches ? matches.length : 0
}

export default function ReportCard({
  artifact,
  status = 'ready',
  onOpen,
}) {
  const isGenerating = status === 'generating'
  const isFailed = status === 'failed'
  const title = artifact?.title
    || (isGenerating ? '报告生成中…' : '分析报告')
  const topic = artifact?.topic || ''
  const wordCount = !isGenerating && !isFailed ? resolveWordCount(artifact) : null

  let subtitle = topic || '点击查看报告'
  if (isFailed) {
    subtitle = '生成失败'
  } else if (isGenerating) {
    subtitle = topic || '正在撰写报告，点击查看实时内容'
  } else if (wordCount != null) {
    subtitle = topic
      ? `${topic} · 约 ${wordCount} 字`
      : `约 ${wordCount} 字`
  }

  return (
    <div className="report-card-row">
      <div className="report-card-avatar-spacer" aria-hidden="true" />
      <button
        type="button"
        className={[
          'report-card',
          'report-card--jump',
          isGenerating ? 'is-generating' : '',
          isFailed ? 'is-failed' : '',
        ].filter(Boolean).join(' ')}
        onClick={() => onOpen?.()}
        title={isGenerating ? '查看实时生成内容' : '打开报告'}
      >
        <div className="report-card-header">
          <div className="report-card-header-left">
            {isGenerating ? (
              <Loader2
                size={15}
                className="report-card-icon report-card-icon--spin"
                strokeWidth={2}
              />
            ) : (
              <FileText size={15} className="report-card-icon" strokeWidth={2} />
            )}
            <div className="report-card-heading">
              <div className="report-card-title">{title}</div>
              <div className="report-card-subtitle">{subtitle}</div>
            </div>
          </div>
          <div className="report-card-actions">
            <ChevronRight size={16} className="report-card-chevron" strokeWidth={2} />
          </div>
        </div>
      </button>
    </div>
  )
}
