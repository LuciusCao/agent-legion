import SmartToyOutlinedIcon from '@mui/icons-material/SmartToyOutlined'
import CodeOutlinedIcon from '@mui/icons-material/CodeOutlined'
import { useStudioNav } from '../shared/useStudioNavState'
import { EntityDraftPublishButton } from './EntityDraftPublishButton'
import { StudioDraftCardHeader } from './StudioDraftCardHeader'
import type {
  AgentDefinitionDraftView,
  NodeCodeDraftView,
} from './studioChatMessages'
import styles from './StudioChatPanel.module.css'

/* #692：Agent 定义 / 节点代码草稿卡。两类草稿是独立实体，发布走各自的
 * 实体端点（EntityDraftPublishButton，codex P1 修正：不能复用 workflow
 * revision 的发布按钮——那发布的是编辑器 YAML，仅实体变更时会因无 diff
 * 而禁用）。发布入口只对来源 tool call「完成」的草稿开放（R2 P2-1）：
 * pending/failed 的保存不保证草稿落库，开放发布会把更早的旧草稿发布
 * 出去、用户误以为新定义已生效。 */

/** 仅来源 tool call 完成的草稿渲染发布入口（R2 P2-1 门控）。 */
function DraftPublishAction({
  status,
  kind,
  entityId,
}: {
  status: string
  kind: 'agent' | 'code'
  entityId: string
}) {
  if (status !== 'completed') return null
  return <EntityDraftPublishButton kind={kind} entityId={entityId} />
}

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
        <DraftPublishAction
          status={draft.status}
          kind="agent"
          entityId={draft.agentId}
        />
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
        <DraftPublishAction
          status={props.draft.status}
          kind="code"
          entityId={props.draft.nodeKey}
        />
      </div>
    </div>
  )
}
