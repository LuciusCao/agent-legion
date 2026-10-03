import { Button, Tooltip } from '@mui/material'
import { useStudioStateOptional } from '../shared/studioStateContext'
import { IDLE_DRAFT_SAVE, type DraftSaveState } from '../shared/draftSaveTypes'
import styles from './WorkflowNodeRuntimeSaveBar.module.css'

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
 * 节点 execution 块（provider/model/thinking）的面板级保存（#769）：形态对齐
 * AgentEditor 的「保存草稿 / 发布」，编辑与保存收敛在同一空间。
 * - 「保存草稿」复用整份草稿的保存通道（useWorkflowDraftPersistence 的
 *   flushNow：跳过 800ms debounce 立即 PUT，带 #633 CAS 基线）；冲突态
 *   禁用——冲突必须在顶部警示里显式二选一，面板不提供隐式 keep-mine。
 * - 「应用到运行」只在全画布改动仅涉及 execution（compare.creates_revision
 *   === false）时可用：走顶栏同一发布确认框（原地更新 active revision 的
 *   运行配置，不产生新版本）。画布另有结构改动时禁用并说明——面板动作
 *   永远不会把用户没打算发布的结构改动一起发布。
 * 脱离 Studio Provider 渲染（Section 级测试）或只读查看时不渲染。
 */
export function WorkflowNodeRuntimeSaveBar(props: {
  nodeKey: string
  readOnly?: boolean
}) {
  const studio = useStudioStateOptional()
  if (!studio || props.readOnly) return null
  const save = studio.draftSave ?? IDLE_DRAFT_SAVE
  const blocked = save.conflict === true || save.loadError === true
  const canSave =
    !blocked && (save.status === 'pending' || save.status === 'error')
  const executionChanged = Boolean(
    studio.compareSummary?.nodeChanges.some(
      (change) =>
        change.nodeKey === props.nodeKey &&
        change.type === 'modified' &&
        change.fields.includes('execution')
    )
  )
  const runtimeOnly = studio.compareSummary?.createsRevision === false
  const applyReason = !runtimeOnly
    ? '画布还有结构改动（节点/连线/声明），需在顶栏「发布」新版本后生效'
    : !studio.canPublish || studio.validating || studio.publishing
      ? '等待草稿保存并校验通过后可应用'
      : ''
  return (
    <div className={styles.saveBar} aria-label="运行配置保存">
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
            variant="contained"
            disabled={!canSave}
            onClick={() => void studio.flushDraftSave()}
          >
            保存草稿
          </Button>
          {executionChanged ? (
            <Tooltip title={applyReason}>
              {/* disabled 时 Tooltip 需要 wrapper span（MUI 约定） */}
              <span>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={applyReason !== '' || blocked}
                  onClick={studio.requestPublish}
                >
                  应用到运行
                </Button>
              </span>
            </Tooltip>
          ) : null}
        </span>
      </div>
      {executionChanged ? (
        <div className={styles.saveHint}>
          {runtimeOnly
            ? '运行配置已随草稿保存；「应用到运行」后对新执行生效，不产生新版本。'
            : '草稿另含结构改动：运行配置随草稿保存，需发布新版本后才生效。'}
        </div>
      ) : null}
    </div>
  )
}
