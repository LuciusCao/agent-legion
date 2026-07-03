import type { ChangeSummaryViewModel } from '../workflowStudioChanges'
import {
  formatEdgeChange,
  formatIntakeChange,
  formatNodeChange,
} from '../workflowStudioChanges'
import { WorkflowPublishReviewDialogChangeCount } from './WorkflowPublishReviewDialogChangeCount'
import { WorkflowPublishReviewDialogRiskChip } from './WorkflowPublishReviewDialogRiskChip'
import styles from './WorkflowPublishReviewDialog.module.css'

type Props = {
  summary: ChangeSummaryViewModel | null
}

export function WorkflowPublishReviewDialogChanges({ summary }: Props) {
  const viewModel = summary
  const hasChanges =
    viewModel &&
    (viewModel.nodeChanges.length > 0 ||
      viewModel.edgeChanges.length > 0 ||
      viewModel.intakeChanges.length > 0 ||
      viewModel.riskFlags.length > 0)

  if (!hasChanges) {
    return <p className={styles.noChanges}>没有可发布的变更</p>
  }

  return (
    <>
      <div className={styles.summaryBar}>
        <WorkflowPublishReviewDialogChangeCount
          count={viewModel.nodeChanges.length}
          label="节点变更"
        />
        <WorkflowPublishReviewDialogChangeCount
          count={viewModel.edgeChanges.length}
          label="边变更"
        />
        <WorkflowPublishReviewDialogChangeCount
          count={viewModel.intakeChanges.length}
          label="Intake 变更"
        />
        {viewModel.riskLevel !== 'none' && (
          <WorkflowPublishReviewDialogRiskChip
            severity={viewModel.riskLevel as 'info' | 'warning' | 'breaking'}
          />
        )}
      </div>

      {viewModel.nodeChanges.length > 0 && (
        <section className={styles.group}>
          <h3 className={styles.groupTitle}>节点变更</h3>
          <ul className={styles.list}>
            {viewModel.nodeChanges.map((change) => (
              <li
                key={`node-${change.type}-${change.nodeKey}`}
                className={styles.item}
              >
                <span className={styles.itemText}>
                  {formatNodeChange(change)}
                </span>
                <WorkflowPublishReviewDialogRiskChip
                  severity={change.severity as 'info' | 'warning' | 'breaking'}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      {viewModel.edgeChanges.length > 0 && (
        <section className={styles.group}>
          <h3 className={styles.groupTitle}>边变更</h3>
          <ul className={styles.list}>
            {viewModel.edgeChanges.map((change) => (
              <li
                key={`edge-${change.type}-${change.source}-${change.target}`}
                className={styles.item}
              >
                <span className={styles.itemText}>
                  {formatEdgeChange(change)}
                </span>
                <WorkflowPublishReviewDialogRiskChip
                  severity={change.severity as 'info' | 'warning' | 'breaking'}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      {viewModel.intakeChanges.length > 0 && (
        <section className={styles.group}>
          <h3 className={styles.groupTitle}>Intake 变更</h3>
          <ul className={styles.list}>
            {viewModel.intakeChanges.map((change) => (
              <li
                key={`intake-${change.type}-${change.modeKey}-${change.fieldKey ?? ''}`}
                className={styles.item}
              >
                <span className={styles.itemText}>
                  {formatIntakeChange(change)}
                </span>
                <WorkflowPublishReviewDialogRiskChip
                  severity={change.severity as 'info' | 'warning' | 'breaking'}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      {viewModel.riskFlags.length > 0 && (
        <section className={styles.group}>
          <h3 className={styles.groupTitle}>风险提示</h3>
          <ul className={styles.list}>
            {viewModel.riskFlags.map((flag) => (
              <li key={`risk-${flag.code}`} className={styles.item}>
                <span className={styles.itemText}>{flag.message}</span>
                <WorkflowPublishReviewDialogRiskChip
                  severity={flag.severity as 'info' | 'warning' | 'breaking'}
                />
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}
