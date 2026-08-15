import { User } from 'lucide-react'
import MarkdownContent from './MarkdownContent.jsx'
import SourcesPanel from './SourcesPanel.jsx'
import './MessageBubble.css'

export default function MessageBubble({
  message,
  isStreaming,
  sources,
  hidden,
  hideAvatar = false,
}) {
  const isAgent = message.sender_id === 'agent'
  const isUser = !isAgent
  const showSources = isAgent && !isStreaming && sources && sources.some(s => s?.url)

  if (hidden) return null

  return (
    <div className={`msg ${isUser ? 'msg--user' : 'msg--agent'}${hideAvatar ? ' msg--no-avatar' : ''}`}>
      {!hideAvatar && (
        <div className={`msg-avatar ${isUser ? 'msg-avatar--user' : 'msg-avatar--agent'}`}>
          {isUser ? <User size={14} /> : 'AI'}
        </div>
      )}

      <div className={`msg-bubble ${isUser ? 'msg-bubble--user' : 'msg-bubble--agent'}`}>
        <div className={`msg-text ${isAgent && isStreaming ? 'msg-text--plain' : ''}`}>
          {isAgent && !isStreaming ? (
            <MarkdownContent content={message.content} sources={sources || []} />
          ) : (
            <>
              {message.content || ''}
              {isStreaming && <span className="msg-cursor" />}
            </>
          )}
        </div>
        {showSources && <SourcesPanel sources={sources} />}
      </div>
    </div>
  )
}
