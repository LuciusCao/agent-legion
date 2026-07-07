import { Button, Chip } from '@mui/material'
import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import styles from './WorkflowStudioCommandBar.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  revision: WorkflowRevisionSummary | null
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
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
  backToDraft: () => void
  useViewedRevisionAsDraft: () => void
}

function riskLabel(
  compareSummary: ChangeSummaryViewModel | null
): string | null {
  if (!compareSummary) return null
  if (compareSummary.riskLevel === 'breaking') return '风险：高'
  if (compareSummary.riskLevel === 'warning') return '风险：中'
  if (compareSummary.riskLevel === 'info') return '风险：低'
  return null
}

export function WorkflowStudioCommandBar({
  workflow,
  revision,
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
  onValidate,
  onPublish,
  onReset,
  backToDraft,
  useViewedRevisionAsDraft,
}: Props) {
  const hash = revision?.definition_hash?.slice(0, 8) ?? '--------'
  const statusText =
    viewMode === 'revision'
      ? `viewing v${revision?.version ?? '-'} · ${hash} · read-only`
      : `${workflow?.key ?? 'workflow'} · draft from v${activeRevision?.version ?? '-'} · ${dirty ? '有未发布变更' : '已同步'}`
  const risk = riskLabel(compareSummary)

  return (
    <div className={styles.commandBar} aria-label="Workflow command bar">
      <div className={styles.identity}>
        <span className={styles.title}>{workflow?.label ?? 'Workflow'}</span>
        <span className={styles.meta}>{statusText}</span>
      </div>
      <div className={styles.status}>
        {compareState === 'loading' && <Chip size="small" label="计算变更" />}
        {risk && <Chip size="small" color="warning" label={risk} />}
        {hasPreservedDraft && <Chip size="small" label="Draft preserved" />}
      </div>
      <div className={styles.actions}>
        {readOnly ? (
          <>
            <Button size="small" variant="outlined" onClick={backToDraft}>
              Back to draft
            </Button>
            <Button
              size="small"
              variant="contained"
              onClick={useViewedRevisionAsDraft}
            >
              Use as draft
            </Button>
          </>
        ) : (
          <>
            <Button
              size="small"
              variant="outlined"
              disabled={!canSubmit || actionState !== 'idle'}
              onClick={onValidate}
            >
              校验
            </Button>
            <Button
              size="small"
              variant="contained"
              disabled={!canPublish || actionState !== 'idle'}
              onClick={onPublish}
            >
              发布
            </Button>
            <Button
              size="small"
              variant="outlined"
              disabled={!dirty || actionState !== 'idle'}
              onClick={onReset}
            >
              重置
            </Button>
          </>
        )}
      </div>
    </div>
  )
}
