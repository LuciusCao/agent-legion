import { useMemo } from 'react'
import { renderMarkdownHtml } from '../../../lib/markdownHtml'
import { textContent, type ChatMessage } from './studioChatMessages'
import type { QueuedMessageState } from './studioChatTurnRecovery'
import bubbleStyles from './StudioChatPanel.module.css'
import markdownStyles from './StudioChatTextBubble.module.css'

type Props = {
  message: ChatMessage
  streaming: boolean
  queueState?: QueuedMessageState | null
}

/** user 与流式中的 agent 文本保持纯文本 pre-wrap（半截 markdown 渲染会抖动）；
 * 完成后的 agent 文本渲染 markdown（marked 解析 + sanitizeHtml 消毒）；
 * 图片一律不自动加载，渲染为点击才打开的链接占位（inertImages）。
 * #882：后端入站排队的用户消息在气泡下标「已排队」/「未送达」。 */
export function StudioChatTextBubble({
  message,
  streaming,
  queueState = null,
}: Props) {
  const text = textContent(message)
  const html = useMemo(
    () =>
      message.role === 'agent' && !streaming
        ? renderMarkdownHtml(text, { inertImages: true })
        : null,
    [message.role, streaming, text]
  )
  const className =
    message.role === 'user' ? bubbleStyles.bubbleUser : bubbleStyles.bubbleAgent
  if (queueState !== null) {
    return (
      <div className={markdownStyles.queuedWrap}>
        <div className={className}>{text}</div>
        <span
          className={
            queueState === 'pending'
              ? markdownStyles.queuedTag
              : markdownStyles.droppedTag
          }
        >
          {queueState === 'pending' ? '已排队' : '未送达，请重发'}
        </span>
      </div>
    )
  }
  if (html === null) return <div className={className}>{text}</div>
  return (
    <div className={className}>
      <div
        className={markdownStyles.markdown}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  )
}
