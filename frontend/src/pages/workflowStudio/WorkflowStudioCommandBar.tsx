import { Chip } from '@mui/material'
import type { WorkflowRevisionSummary } from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import { WorkflowRevisionSelect } from './WorkflowRevisionSelect'
import { WorkflowStudioCommandBarActions } from './WorkflowStudioCommandBarActions'
import styles from './WorkflowStudioCommandBar.module.css'

type Props = {
  revision: WorkflowRevisionSummary | null
  revisions: WorkflowRevisionSummary[]
  activeRevision: WorkflowRevisionSummary | null
  viewMode: 'draft' | 'revision'
  dirty: boolean
  readOnly: boolean
  hasPreservedDraft: boolean
  compareSummary: ChangeSummaryViewModel | null
  compareState: 'idle' | 'loading' | 'ready' | 'error'
  actionState: 'idle' | 'validating' | 'publishing'
  canSubmit: boolean
  canPublish: boolean
  selectedRevisionId?: string | null
  isLoadingRevision?: boolean
  revisionLoadError?: string | null
  onSelectRevision: (revisionId: string) => void
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
  backToDraft: () => void
  useViewedRevisionAsDraft: () => void
}

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
  backToDraft,
  useViewedRevisionAsDraft,
}: Props) {
  const hash = revision?.definition_hash?.slice(0, 8) ?? '--------'
  const modeText =
    viewMode === 'revision'
      ? `viewing v${revision?.version ?? '-'} · ${hash} · read-only`
      : `draft from v${activeRevision?.version ?? '-'}`
  const syncText = readOnly ? '只读' : dirty ? '有未发布变更' : '已同步'
  const risk =
    compareSummary?.riskLevel === 'breaking'
      ? '风险：高'
      : compareSummary?.riskLevel === 'warning'
        ? '风险：中'
        : compareSummary?.riskLevel === 'info'
          ? '风险：低'
          : null

  return (
    <div className={styles.commandBar} aria-label="Workflow command bar">
      <span className={styles.meta}>{modeText}</span>
      <div className={styles.status}>
        <Chip size="small" label={syncText} />
        {compareState === 'loading' && <Chip size="small" label="计算变更" />}
        {risk && <Chip size="small" color="warning" label={risk} />}
        {hasPreservedDraft && <Chip size="small" label="Draft preserved" />}
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
