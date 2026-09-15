import { useStudioNav } from '../shared/useStudioNavState'
import type {
  AgentDefinitionDraftView,
  NodeCodeDraftView,
} from './studioChatMessages'
import styles from './StudioChatPanel.module.css'

export function AgentDefinitionDraftCard({
  draft,
}: {
  draft: AgentDefinitionDraftView
}) {
  const nav = useStudioNav()
  const meta = [draft.runtime ? `runtime: ${draft.runtime}` : null]
    .filter(Boolean)
    .join(' · ')
  return (
    <div className={styles.draftCard}>
      <div className={styles.draftTitle}>
        🤖 Agent 定义草稿：{draft.agentId}
      </div>
      {meta && <div className={styles.draftMeta}>{meta}</div>}
      <div className={styles.draftActions}>
        <button
          type="button"
          className={styles.draftButton}
          onClick={() => nav.openAgent(draft.agentId)}
        >
          查看草稿
        </button>
      </div>
    </div>
  )
}

export function NodeCodeDraftCard(props: {
  draft: NodeCodeDraftView
  onSelectNode?: (nodeKey: string) => void
}) {
  return (
    <div className={styles.draftCard}>
      <div className={styles.draftTitle}>
        🧩 节点代码草稿：{props.draft.nodeKey}
      </div>
      <div className={styles.draftMeta}>仅草稿，发布前不会在 job 中运行</div>
      {props.onSelectNode && (
        <div className={styles.draftActions}>
          <button
            type="button"
            className={styles.draftButton}
            onClick={() => props.onSelectNode!(props.draft.nodeKey)}
          >
            查看草稿
          </button>
        </div>
      )}
    </div>
  )
}
