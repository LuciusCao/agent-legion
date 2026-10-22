import { useState } from 'react'
import CheckCircleOutlinedIcon from '@mui/icons-material/CheckCircleOutlined'
import HistoryOutlinedIcon from '@mui/icons-material/HistoryOutlined'
import TuneOutlinedIcon from '@mui/icons-material/TuneOutlined'
import { fetchWorkflowRuntimeVersions } from '../../../api'
import { WorkflowRuntimeVersionsDialog } from '../inspector/WorkflowRuntimeVersionsDialog'
import { useStudioStateOptional } from '../shared/studioStateContext'
import {
  computeDraftChannels,
  formatRuntimeKey,
  runtimeCardSettlementKey,
  runtimeValueAt,
} from './draftChannelSplit'
import {
  useRuntimeCardSettlement,
  useRuntimeCardSettlementStore,
} from './runtimeCardSettlement'
import type { WorkflowDraftView } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'
import splitStyles from './draftChannelSplit.module.css'

function formatSavedAt(ms: number): string {
  return new Date(ms).toLocaleTimeString('zh-CN', { hour12: false })
}

/** 落定登记（事件上下文）：版本号取生效后最新一条配置版本（本次保存追加的
 * 那条）；版本查询失败不阻塞落定态，仅不标版本号。 */
async function recordSettlement(
  workspaceId: string,
  cardKey: string,
  savedKeys: string[]
) {
  let version: number | null
  try {
    const result = await fetchWorkflowRuntimeVersions(workspaceId)
    version =
      Math.max(0, ...(result.versions ?? []).map((v) => v.version)) || null
  } catch {
    version = null
  }
  useRuntimeCardSettlementStore.getState().settleRuntimeCard(cardKey, {
    version,
    savedKeys,
    savedAt: Date.now(),
  })
}

/**
 * M65 拆卡（用户确认原型）：「运行配置变更」卡——agent 建议的 runtime 键
 * diff（相对最新生效基线）+「应用并生效」（即改即存、追加配置版本、不进
 * revision）+「版本历史」入口。与「Workflow 草稿」卡操作对象不相交，固定
 * 呈现在前。应用成功后转入「已生效 · vN」落定态（用户拍板：卡片不消失，
 * 建议原文灰色备查）；无 studio 上下文、解析失败、无 runtime 差异且未落定
 * 时不渲染（由 Workflow 草稿卡回落经典形态）。
 */
export function RuntimeConfigCard(props: {
  draft: WorkflowDraftView
  workspaceId: string
}) {
  const studio = useStudioStateOptional()
  const [historyOpen, setHistoryOpen] = useState(false)
  const cardKey = runtimeCardSettlementKey(props.workspaceId, props.draft)
  const settlement = useRuntimeCardSettlement(cardKey)
  const channels = computeDraftChannels({
    proposalYaml: props.draft.yaml,
    originalYaml: studio?.originalYaml,
    currentDraftYaml: studio?.draftYaml ?? '',
  })
  if (!studio || !channels) return null
  if (!settlement && channels.runtimeKeys.length === 0) return null

  async function apply() {
    if (!studio || settlement) return
    const ok = await studio.saveRuntimeConfig(props.draft.yaml)
    if (ok) {
      await recordSettlement(
        props.workspaceId,
        cardKey,
        channels?.runtimeKeys ?? []
      )
    }
  }

  const historyButton = (
    <button
      type="button"
      className={styles.draftButton}
      onClick={() => setHistoryOpen(true)}
    >
      <HistoryOutlinedIcon className={splitStyles.historyIcon} />
      版本历史
    </button>
  )
  const settledKeys = settlement?.savedKeys ?? []
  const shownKeys = settlement ? settledKeys : channels.runtimeKeys

  return (
    <div className={styles.draftCard}>
      <div className={`${styles.draftTitle} ${splitStyles.titleRuntime}`}>
        <TuneOutlinedIcon className={splitStyles.iconRuntime} />
        <span>运行配置变更</span>
        {settlement ? (
          <span className={`${splitStyles.badge} ${splitStyles.badgeRuntime}`}>
            {settlement.version !== null
              ? `已生效 · v${settlement.version}`
              : '已生效'}
          </span>
        ) : (
          <span className={`${splitStyles.badge} ${splitStyles.badgeRuntime}`}>
            运行通道 · 立即生效
          </span>
        )}
      </div>
      <div className={styles.draftMeta}>
        {settlement
          ? `agent 建议的运行配置修改 · 已于 ${formatSavedAt(settlement.savedAt)} 生效`
          : `agent 建议的运行配置修改 · 相对当前生效配置（${channels.runtimeKeys.length} 项）`}
      </div>
      <div className={splitStyles.body}>
        {shownKeys.map((key) => (
          <div
            key={key}
            className={
              settlement
                ? `${splitStyles.diffRow} ${splitStyles.diffRowSettled}`
                : splitStyles.diffRow
            }
          >
            <span className={splitStyles.diffField}>
              {formatRuntimeKey(key)}
            </span>
            <span>→</span>
            <span className={splitStyles.diffValue}>
              {runtimeValueAt(channels.proposal, key)}
            </span>
          </div>
        ))}
        {settlement ? (
          <div className={`${splitStyles.hint} ${splitStyles.hintSettled}`}>
            <CheckCircleOutlinedIcon />
            {settlement.version !== null
              ? `已对新执行生效（配置版本 v${settlement.version}）；生效前的建议原文保留在此备查。`
              : '已对新执行生效；生效前的建议原文保留在此备查。'}
          </div>
        ) : (
          <div className={splitStyles.hint}>
            应用后立即对新执行生效并追加一条配置版本，不创建新
            revision；进行中的任务不受影响。
          </div>
        )}
      </div>
      <div className={styles.draftActions}>
        {!settlement && (
          <button
            type="button"
            className={`${styles.draftButton} ${styles.draftPrimary}`}
            disabled={studio.runtimeSaving}
            onClick={() => void apply()}
          >
            应用并生效
          </button>
        )}
        {historyButton}
      </div>
      <WorkflowRuntimeVersionsDialog
        open={historyOpen}
        workspaceId={props.workspaceId}
        readOnly={studio.viewMode === 'revision'}
        onClose={() => setHistoryOpen(false)}
      />
    </div>
  )
}
