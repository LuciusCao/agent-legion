import SmartToyOutlinedIcon from '@mui/icons-material/SmartToyOutlined'
import CodeOutlinedIcon from '@mui/icons-material/CodeOutlined'
import { useStudioNav } from '../shared/useStudioNavState'
import { StudioDraftCardHeader } from './StudioDraftCardHeader'
import { WorkflowDraftPublishButton } from './WorkflowDraftPublishAction'
import type {
  AgentDefinitionDraftView,
  NodeCodeDraftView,
} from './studioChatMessages'
import styles from './StudioChatPanel.module.css'

/* #692：Agent 定义 / 节点代码草稿卡。与 Workflow 卡共用发布入口
 * （WorkflowDraftPublishButton）：两类草稿保存即入服务端草稿区，发布
 * 随 revision 冻结——发布对象语义与顶栏一致（编辑器 YAML + 已保存的
 * 服务端草稿），卡片上直接可发起，不必跳编辑器找顶栏。 */

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
      <StudioDraftCardHeader icon={SmartToyOutlinedIcon} tone="agent">
        Agent 定义草稿：{draft.agentId}
      </StudioDraftCardHeader>
      {meta && <div className={styles.draftMeta}>{meta}</div>}
      <div className={styles.draftActions}>
        <button
          type="button"
          className={styles.draftButton}
          onClick={() => nav.openAgent(draft.agentId)}
        >
          查看草稿
        </button>
        <WorkflowDraftPublishButton />
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
      <StudioDraftCardHeader icon={CodeOutlinedIcon} tone="code">
        节点代码草稿：{props.draft.nodeKey}
      </StudioDraftCardHeader>
      <div className={styles.draftMeta}>
        已存为服务端草稿，发布后新执行才使用
      </div>
      {/* 定位与发布是独立能力：无 onSelectNode（无定位链路的调用方）
       * 只少了「查看草稿」，发布入口不受影响。 */}
      <div className={styles.draftActions}>
        {props.onSelectNode && (
          <button
            type="button"
            className={styles.draftButton}
            onClick={() => props.onSelectNode!(props.draft.nodeKey)}
          >
            查看草稿
          </button>
        )}
        <WorkflowDraftPublishButton />
      </div>
    </div>
  )
}
