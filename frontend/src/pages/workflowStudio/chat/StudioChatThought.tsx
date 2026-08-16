import styles from './StudioChatThought.module.css'

/** agent 的思考过程（ACP agent_thought_chunk 流式聚合）：默认折叠的
 * <details> 区块，浅色左边条与正文气泡区分，避免刷屏。 */
export function StudioChatThought({ text }: { text: string }) {
  if (!text.trim()) return null
  return (
    <details className={styles.thought}>
      <summary>思考过程</summary>
      <div className={styles.thoughtBody}>{text}</div>
    </details>
  )
}
