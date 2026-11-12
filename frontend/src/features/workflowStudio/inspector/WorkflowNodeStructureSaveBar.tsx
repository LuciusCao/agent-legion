import { Button } from '@mui/material'
import { useStudioStateOptional } from '../shared/studioStateContext'
import { IDLE_DRAFT_SAVE, type DraftSaveState } from '../shared/draftSaveTypes'
import { hasStructuralChanges } from '../validation/workflowStudioChanges'
import styles from './WorkflowNodeStructureSaveBar.module.css'

/** 面板内的保存状态一句话（#769）：与顶栏瞬态文本同源（draftSave 状态机），
 * 但成功态也常驻——面板里改完要能当场看到「已落草稿」。 */
function saveStatusText(save: DraftSaveState): string {
  if (save.conflict) return '草稿冲突：本页编辑未落盘，请先在顶部警示中选择版本'
  if (save.loadError) return '草稿服务不可用，编辑仅保留在本页'
  if (save.status === 'pending') return '有未保存的修改'
  if (save.status === 'saving') return '保存中…'
  if (save.status === 'error') return '保存失败'
  // codex P2（#897）：成功文案只给能证明「已落盘」的状态——saved（本页
  // PUT 成功），或带 savedAt 的 idle（hydrate 记下了服务端草稿基线且其后
  // 无待存编辑）。无 savedAt 的 idle 可能是服务端草稿查询未完成（此期间
  // 编辑不调度 PUT），也可能是尚无服务端草稿——一律不宣称已保存。
  if (save.status === 'idle' && !save.savedAt) return '草稿尚未保存（同步中）'
  if (!save.savedAt) return '已保存到草稿'
  const at = new Date(save.savedAt)
  return Number.isNaN(at.getTime())
    ? '已保存到草稿'
    : `已保存到草稿 · ${at.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
}

/**
 * 结构通道保存条（M48，自 #769 的 WorkflowNodeRuntimeSaveBar 改造）：
 * 面板底部收敛「保存草稿 / 发布」——结构改动随草稿保存、发布新版本后
 * 生效，此条链路不提供「立即生效」（运行配置的即改即存在「运行配置」
 * 专区，双通道分离）。
 * 「保存草稿」复用整份草稿的保存通道（useWorkflowDraftPersistence 的
 * flushNow：跳过 debounce 立即 PUT，带 #633 CAS 基线）；冲突态禁用——
 * 冲突必须在顶部警示里显式二选一，面板不提供隐式 keep-mine。
 * 脱离 Studio Provider 渲染（Section 级测试）或只读查看时不渲染。
 */
export function WorkflowNodeStructureSaveBar(props: { readOnly?: boolean }) {
  const studio = useStudioStateOptional()
  if (!studio || props.readOnly) return null
  const save = studio.draftSave ?? IDLE_DRAFT_SAVE
  const blocked = save.conflict === true || save.loadError === true
  const canSave =
    !blocked && (save.status === 'pending' || save.status === 'error')
  const structuralChanges = hasStructuralChanges(studio.compareSummary)
  return (
    <div className={styles.saveBar} aria-label="结构保存">
      <div className={styles.saveRow}>
        <span
          className={blocked ? styles.saveWarning : styles.saveStatus}
          role="status"
        >
          {saveStatusText(save)}
        </span>
        <span className={styles.saveActions}>
          <Button
            size="small"
            variant="outlined"
            disabled={!canSave}
            onClick={() => void studio.flushDraftSave()}
          >
            保存草稿
          </Button>
          {structuralChanges ? (
            <Button
              size="small"
              variant="contained"
              disabled={
                !studio.canPublish || studio.validating || studio.publishing
              }
              onClick={studio.requestPublish}
            >
              发布…
            </Button>
          ) : null}
        </span>
      </div>
      {structuralChanges ? (
        <div className={styles.saveHint}>
          结构改动：已随草稿保存，需在顶栏「发布」新版本后生效。此条链路不提供「立即生效」。
        </div>
      ) : null}
    </div>
  )
}
