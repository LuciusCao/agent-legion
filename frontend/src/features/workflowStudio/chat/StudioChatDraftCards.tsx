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
 * 出去、用户误以为新定义已生效。同一实体多次保存时 extractor 只保留
 * 最新一张卡（codex P1 第二轮）——发布请求只带实体 ID，服务端发布的
 * 是当前草稿，旧卡的按钮会无提示地发布另一份内容。 */

/** 仅来源 tool call 完成的草稿渲染发布入口（R2 P2-1 门控），并把保存
 * 响应的草稿身份 hash 传给发布按钮（codex P1 第三轮：发布前与服务端
 * 当前草稿比对）。 */
function DraftPublishAction({
  status,
  kind,
  entityId,
  draftHash,
  workspaceId,
}: {
  status: string
  kind: 'agent' | 'code'
  entityId: string
  draftHash: string | null
  /** 路由/调用方传入的 workspace（R4 P1：不能读全局 store——job 排查/
   * 定制预览载体在别的 workspace 下渲染，store 里的值是别处的）。 */
  workspaceId: string
}) {
  if (status !== 'completed') return null
  return (
    <EntityDraftPublishButton
      kind={kind}
      entityId={entityId}
      draftHash={draftHash}
      workspaceId={workspaceId}
    />
  )
}

export function AgentDefinitionDraftCard({
  draft,
  workspaceId,
}: {
  draft: AgentDefinitionDraftView
  workspaceId: string
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
          draftHash={draft.draftHash}
          workspaceId={workspaceId}
        />
      </div>
    </div>
  )
}

/** 节点代码卡的草稿状态文案（R3 P2-2：按来源 tool call 的 status 分支
 * ——去重后只剩最新一张卡，「已存为服务端草稿」对 failed/pending 的
 * 保存是假话：草稿未落库，服务端还是上一份 completed 的内容）。 */
function nodeDraftStatusText(status: string): string {
  if (status === 'completed') return '已存为服务端草稿，发布后新执行才使用'
  if (status === 'failed') return '本次保存失败，草稿未更新'
  return '保存中…'
}

export function NodeCodeDraftCard(props: {
  draft: NodeCodeDraftView
  workspaceId: string
  onSelectNode?: (nodeKey: string) => void
}) {
  return (
    <div className={styles.draftCard}>
      <StudioDraftCardHeader icon={CodeOutlinedIcon} tone="code">
        节点代码草稿：{props.draft.nodeKey}
      </StudioDraftCardHeader>
      <div className={styles.draftMeta}>
        {nodeDraftStatusText(props.draft.status)}
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
          draftHash={props.draft.draftHash}
          workspaceId={props.workspaceId}
        />
      </div>
    </div>
  )
}
