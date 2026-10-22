import AccountTreeOutlinedIcon from '@mui/icons-material/AccountTreeOutlined'
import { useStudioStateOptional } from '../shared/studioStateContext'
import { StudioDraftCardHeader } from './StudioDraftCardHeader'
import { computeDraftChannels, runtimeCardKey } from './draftChannelSplit'
import { useRuntimeCardSettlement } from './runtimeCardSettlement'
import { useWorkflowDraftDiffDialog } from './WorkflowDraftDiffDialog'
import { WorkflowStructureCard } from './WorkflowStructureCard'
import {
  WorkflowDraftPublishButton,
  WorkflowDraftStaleHint,
} from './WorkflowDraftPublishAction'
import type { WorkflowDraftView } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'

type WorkflowProps = {
  draft: WorkflowDraftView
  workspaceId: string
  onApply: (yaml: string) => void
}

/* M65 拆卡（用户确认原型）：「Workflow 草稿」卡——分发器 + 经典回落形态。
 * 有 studio 上下文且建议含结构变更时交给 WorkflowStructureCard（只承载结构
 * 语义）；纯 runtime 建议或运行配置已落定时返回 null（由 RuntimeConfigCard
 * 承载）；无 studio 上下文（测试直渲染）、建议 YAML 解析失败、或与现状完全
 * 无差异时回落经典单卡（与 M48 之前形态一致）。 */
export function WorkflowDraftCard(props: WorkflowProps) {
  const studio = useStudioStateOptional()
  const settlement = useRuntimeCardSettlement(runtimeCardKey(props.draft))
  const diffDialog = useWorkflowDraftDiffDialog({
    workspaceId: props.workspaceId,
    draftYaml: props.draft.yaml,
  })
  const channels = computeDraftChannels({
    proposalYaml: props.draft.yaml,
    originalYaml: studio?.originalYaml,
    currentDraftYaml: studio?.draftYaml ?? '',
  })

  if (channels && channels.structuralCount > 0) {
    return (
      <WorkflowStructureCard
        draft={props.draft}
        workspaceId={props.workspaceId}
        channels={channels}
        onApply={props.onApply}
      />
    )
  }
  // 纯 runtime 建议 / 运行配置已落定：本卡让位给 RuntimeConfigCard。
  if (channels && (channels.runtimeKeys.length > 0 || settlement)) {
    return null
  }

  // 经典形态：无 studio 上下文 / 解析失败 / 与现状无差异。
  return (
    <div className={styles.draftCard}>
      <StudioDraftCardHeader icon={AccountTreeOutlinedIcon} tone="workflow">
        Workflow 草稿
      </StudioDraftCardHeader>
      <div className={styles.draftMeta}>
        {props.draft.compareMeta ?? 'agent 产出的定义草稿'}
        {props.draft.validated ? ' · 校验通过' : ' · 未通过校验'}
      </div>
      <div className={styles.draftActions}>
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
          onClick={() => props.onApply(props.draft.yaml)}
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
