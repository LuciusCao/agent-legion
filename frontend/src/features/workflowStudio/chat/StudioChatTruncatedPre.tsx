import { useState } from 'react'
import { outputPreview } from './studioChatTruncation'
import styles from './StudioChatPanel.module.css'

/** 长文本的按需渲染出口（#1120）：默认只把前 OUTPUT_PREVIEW_CHAR_LIMIT
 * 字符放进 DOM，点击「查看完整…」才渲染全量——超长输出的 DOM/内存成本
 * 只在用户点开后支付。截断只发生在渲染层，传入的原文不变。 */
export function StudioChatTruncatedPre({
  text,
  expandLabel,
}: {
  text: string
  expandLabel: string
}) {
  const [expanded, setExpanded] = useState(false)
  const { preview, truncated } = outputPreview(text)
  if (!truncated || expanded) return <pre>{text}</pre>
  return (
    <>
      <pre>{preview}</pre>
      <button
        type="button"
        className={styles.draftButton}
        onClick={() => setExpanded(true)}
      >
        {expandLabel}（共 {text.length} 字符）
      </button>
    </>
  )
}
