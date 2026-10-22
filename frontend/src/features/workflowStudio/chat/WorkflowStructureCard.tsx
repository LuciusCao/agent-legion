import AccountTreeOutlinedIcon from '@mui/icons-material/AccountTreeOutlined'
import CampaignOutlinedIcon from '@mui/icons-material/CampaignOutlined'
import { combineYamlStructureRuntime } from '../shared/workflowRuntimeSlice'
import { useStudioStateOptional } from '../shared/studioStateContext'
import { useAgentPublishNoticeStore } from '../shared/agentPublishNoticeStore'
import { StudioDraftCardHeader } from './StudioDraftCardHeader'
import { useWorkflowDraftDiffDialog } from './WorkflowDraftDiffDialog'
import {
  WorkflowDraftPublishButton,
  WorkflowDraftStaleHint,
} from './WorkflowDraftPublishAction'
import type { DraftChannels } from './draftChannelSplit'
import type { WorkflowDraftView } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'
import splitStyles from './draftChannelSplit.module.css'

/* M65 拆卡（用户确认原型）：「Workflow 草稿」卡的结构形态——只承载结构变更：
 * 查看 diff / 应用到编辑器（结构切片进编辑器草稿，保留当前 runtime 切片，
 * 即 M48「存为草稿」与「应用到编辑器」合并后的单一主按钮）/ 发布新版本。
 * 发布请求挂起时顶部给引导条：复核框只含结构、运行配置卡无需先处理
 * （两卡操作对象不相交，发布不等待 runtime 卡）。 */
export function WorkflowStructureCard(props: {
  draft: WorkflowDraftView
  workspaceId: string
  channels: DraftChannels
  onApply: (yaml: string) => void
}) {
  const studio = useStudioStateOptional()
  const publishRequestPending = useAgentPublishNoticeStore(
    (state) => state.pendingRequestId !== null
  )
  const diffDialog = useWorkflowDraftDiffDialog({
    workspaceId: props.workspaceId,
    draftYaml: props.draft.yaml,
  })
  const structural = props.channels.structural

  return (
    <div className={styles.draftCard}>
      <StudioDraftCardHeader icon={AccountTreeOutlinedIcon} tone="workflow">
        Workflow 草稿
        <span className={`${splitStyles.badge} ${splitStyles.badgeStructure}`}>
          结构通道 · 发布生效
        </span>
      </StudioDraftCardHeader>
      <div className={styles.draftMeta}>
        {props.draft.compareMeta ?? 'agent 产出的定义草稿'}
        {props.draft.validated ? ' · 校验通过' : ' · 未通过校验'}
        {` · 仅结构变更（${props.channels.structuralCount} 项）`}
      </div>
      {publishRequestPending && (
        <div className={splitStyles.pendingBanner} role="note">
          <CampaignOutlinedIcon />
          <div>
            <b>Agent 已请求发布本次结构变更。</b>
            复核在发布复核框中进行，只包含结构变更；上方「运行配置变更」卡无需先处理——发布只发结构，运行配置可随时独立应用。
          </div>
        </div>
      )}
      <div className={splitStyles.body}>
        {structural.nodeKeys.map((key) => (
          <div key={key} className={splitStyles.diffRow}>
            <span className={splitStyles.diffField}>{key}</span>
          </div>
        ))}
        {structural.edgesChanged && (
          <div className={splitStyles.diffRow}>
            <span className={splitStyles.diffField}>连线（edges）</span>
          </div>
        )}
        {structural.otherChanged && (
          <div className={splitStyles.diffRow}>
            <span className={splitStyles.diffField}>
              顶层声明（label / intake 等）
            </span>
          </div>
        )}
      </div>
      <div className={splitStyles.hint}>
        应用到编辑器后随顶栏「发布」生成新版本生效；不影响当前生效的运行配置。
      </div>
      <div className={`${styles.draftActions} ${splitStyles.actions}`}>
        <button
          type="button"
          className={styles.draftButton}
          onClick={diffDialog.openDialog}
        >
          查看 diff
        </button>
        <button
          type="button"
          className={`${styles.draftButton} ${styles.draftPrimary}`}
          onClick={() =>
            studio &&
            props.onApply(
              combineYamlStructureRuntime(props.draft.yaml, studio.draftYaml)
            )
          }
        >
          应用到编辑器
        </button>
        <WorkflowDraftPublishButton />
      </div>
      <WorkflowDraftStaleHint
        draftYaml={props.draft.yaml}
        draftHash={props.draft.draftHash}
      />
      {diffDialog.dialog}
    </div>
  )
}
