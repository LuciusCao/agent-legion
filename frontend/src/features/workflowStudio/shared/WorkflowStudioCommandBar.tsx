import { draftSaveText } from './useWorkflowDraftPersistence'
import { WorkflowRevisionSelect } from './WorkflowRevisionSelect'
import { WorkflowStudioCommandBarActions } from './WorkflowStudioCommandBarActions'
import type { WorkflowStudioCommandBarProps as Props } from './WorkflowStudioCommandBar.types'
import { WorkflowStudioStatusChip } from './WorkflowStudioStatusChip'
import styles from './WorkflowStudioCommandBar.module.css'

export function WorkflowStudioCommandBar({
  revision,
  revisions,
  activeRevision,
  viewMode,
  dirty,
  readOnly,
  hasPreservedDraft,
  compareSummary,
  compareState,
  draftSave,
  actionState,
  canSubmit,
  canPublish,
  selectedRevisionId,
  isLoadingRevision,
  revisionLoadError,
  onSelectRevision,
  onValidate,
  onPublish,
  onReset,
  onShowChanges,
  backToDraft,
  useViewedRevisionAsDraft,
}: Props) {
  const hash = revision?.definition_hash?.slice(0, 8) ?? '--------'
  const modeText =
    viewMode === 'revision'
      ? `查看 v${revision?.version ?? '-'} · ${hash} · 只读`
      : `基于 v${activeRevision?.version ?? '-'} 的草稿`
  // 草稿自动保存状态低噪暴露：不进 chip（StatusChip 体积预算已满），只挂
  // 顶栏 meta 文本的 tooltip。
  const saveText = draftSaveText(draftSave)

  return (
    <div className={styles.commandBar} aria-label="Workflow command bar">
      <span className={styles.meta} title={saveText ?? undefined}>
        {modeText}
      </span>
      <div className={styles.status}>
        <WorkflowStudioStatusChip
          readOnly={readOnly}
          version={revision?.version ?? null}
          dirty={dirty}
          hasPreservedDraft={hasPreservedDraft}
          summary={compareSummary}
          compareState={compareState}
          onShowChanges={onShowChanges}
        />
      </div>
      <WorkflowRevisionSelect
        revisions={revisions}
        activeRevisionId={activeRevision?.id}
        selectedRevisionId={selectedRevisionId}
        currentVersion={revision?.version ?? activeRevision?.version}
        currentHash={
          revision?.definition_hash ?? activeRevision?.definition_hash ?? null
        }
        disabled={isLoadingRevision}
        error={revisionLoadError}
        onSelectRevision={onSelectRevision}
      />
      <div className={styles.actions}>
        <WorkflowStudioCommandBarActions
          readOnly={readOnly}
          dirty={dirty}
          actionState={actionState}
          canSubmit={canSubmit}
          canPublish={canPublish}
          createsRevision={compareSummary?.createsRevision}
          onValidate={onValidate}
          onPublish={onPublish}
          onReset={onReset}
          backToDraft={backToDraft}
          useViewedRevisionAsDraft={useViewedRevisionAsDraft}
        />
      </div>
    </div>
  )
}
