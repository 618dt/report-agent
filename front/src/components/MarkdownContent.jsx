/**
 * Markdown 内容渲染组件
 *
 * 使用 react-markdown + remark-gfm 渲染 AI 生成的 Markdown 文本。
 * 支持：
 * - 标准 Markdown（标题、粗体、列表、表格、代码块等）
 * - 引用标注 [1] [^1] → 可点击的上标徽章（有对应 URL 时）
 * - 无对应来源的引用标记直接隐藏，避免裸露 [N]
 * - 裸 URL 自动链接
 * - 代码块语法（无高亮，使用等宽字体）
 */
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { sourceHasDisplay, stripSourcesSection } from './sources.js'
import './MarkdownContent.css'

export default function MarkdownContent({ content, sources = [] }) {
  if (!content) return null

  const hasSources = sources.some(sourceHasDisplay)

  // 有 SourcesPanel 时去掉正文末尾「参考来源」章节，避免重复展示
  let text = content
  if (hasSources) {
    text = stripSourcesSection(text)
  }

  // 修复 CommonMark 对中文闭式标点旁 **加粗** 解析失败（如 **《标题》**）
  text = _fixCjkEmphasis(text)

  // 预处理：将 [N] 和 [^N] 引用标记替换为 Markdown 链接格式
  // 这样 react-markdown 能正确渲染，然后由 LinkRenderer 检测并渲染为徽章
  const processedContent = _preprocessCitations(text, sources)

  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: LinkRenderer,
          code: CodeRenderer,
          pre: PreRenderer,
          table: TableRenderer,
          img: ImgRenderer,
        }}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  )
}

/**
 * 修复中文标点旁的加粗/斜体解析
 *
 * CommonMark 要求结束 `**` 为 right-flanking：若前面是标点（如 》），
 * 后面还必须是空白或标点。`**《标题》**约` 中结束符后紧跟汉字，加粗失效。
 * 在闭式标点与结束分隔符之间插入零宽空格即可。
 */
function _fixCjkEmphasis(text) {
  if (!text) return text

  // 兼容模型偶发写出的 ** 《标题》 **（分隔符内侧多余空白）
  let result = text.replace(
    /(\*\*|__)\s+([《「『""（【][\s\S]*?[》」』""）】])\s+(\*\*|__)/g,
    '$1$2$3',
  )

  // **《标题》** / __「标题」__ 等：在闭式标点后插入 ZWSP
  result = result.replace(
    /(\*\*|__)([《「『""（【][\s\S]*?[》」』""）】])(\*\*|__)/g,
    (_, open, inner, close) => `${open}${inner}\u200B${close}`,
  )

  return result
}

/**
 * 预处理引用标注
 *
 * 将文本中的 [1]、[2]、[^1]、[^2] 等引用标记替换为 Markdown 链接，
 * 链接目标指向对应的来源 URL。
 *
 * - 有对应 URL：转为 [N](url)，由 LinkRenderer 渲染为徽章
 * - 无对应 URL / 越界：直接删除标记，避免出现裸 [5]
 *
 * 注意：不替换已经是 Markdown 链接一部分的方括号（如 [text](url)）
 */
function _preprocessCitations(text, sources) {
  // 先将已有的 Markdown 链接占位保护，避免误替换
  const links = []
  const protected_ = text.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    (match, label, url) => {
      links.push({ label, url })
      return `__LINK_${links.length - 1}__`
    }
  )

  const replaceCitation = (match, num) => {
    const idx = parseInt(num, 10) - 1
    const src = Array.isArray(sources) ? sources[idx] : null
    if (src?.url) {
      const title = (src.title || '').replace(/"/g, '')
      return title ? `[${num}](${src.url} "${title}")` : `[${num}](${src.url})`
    }
    if (src?.title) {
      const title = src.title.replace(/"/g, '')
      return `[${num}](#cite-${num} "${title}")`
    }
    // 无对应来源：仍显示角标，避免把行内 [N] 吞掉
    return `[${num}](#cite-${num})`
  }

  // 替换 [^N] 格式（脚注式引用）
  let result = protected_.replace(/\[\^(\d+)\]/g, replaceCitation)

  // 替换 [N] 格式（标准引用）
  result = result.replace(/\[(\d+)\]/g, replaceCitation)

  // 还原被保护的 Markdown 链接
  result = result.replace(
    /__LINK_(\d+)__/g,
    (match, idx) => {
      const link = links[parseInt(idx, 10)]
      return link ? `[${link.label}](${link.url})` : match
    }
  )

  return result
}

/**
 * 链接渲染器
 * - 外部链接：新窗口打开，带安全属性
 * - 引用标注 [N]：渲染为蓝色可点击上标徽章
 */
function LinkRenderer({ href, children, title, ...props }) {
  if (!href) return <span>{children}</span>

  const text = String(children)
  // 检测引用标注：纯数字如 "1", "2" 或带括号如 "[1]"
  const isCitation = /^\[?\d+\]?$/.test(text)

  if (isCitation) {
    const label = text.replace(/[\[\]]/g, '')
    const tooltip = title || href
    if (href.startsWith('#cite-')) {
      return (
        <span className="citation-badge citation-badge--plain" title={tooltip}>
          {label}
        </span>
      )
    }
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="citation-badge"
        title={tooltip}
        {...props}
      >
        {label}
      </a>
    )
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="markdown-link"
      {...props}
    >
      {children}
    </a>
  )
}

/**
 * 行内代码渲染器
 *
 * 无 language- 的围栏代码块也会生成无 className 的 <code>，
 * 不能仅凭 !className 判断行内，否则会套上浅色 inline-code 底，叠在深色 pre 上难读。
 */
function CodeRenderer({ className, children, ...props }) {
  const text = String(children ?? '')
  const isBlock = Boolean(className) || text.includes('\n')
  if (!isBlock) {
    return <code className="inline-code" {...props}>{children}</code>
  }
  return (
    <code className={className || undefined} {...props}>
      {children}
    </code>
  )
}

/**
 * 代码块渲染器
 */
function PreRenderer({ children, ...props }) {
  return <pre className="code-block" {...props}>{children}</pre>
}

/**
 * 表格渲染器 — 添加滚动容器
 */
function TableRenderer({ children, ...props }) {
  return (
    <div className="table-wrapper">
      <table {...props}>{children}</table>
    </div>
  )
}

/**
 * 图片渲染器 — 限制最大宽度
 */
function ImgRenderer(props) {
  return <img className="markdown-img" loading="lazy" {...props} />
}
