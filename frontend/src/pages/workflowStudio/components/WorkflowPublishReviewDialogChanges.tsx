import type { ChangeSummaryViewModel } from '../workflowStudioChanges'
import {
  formatEdgeChange,
  formatIntakeChange,
  formatMetadataChange,
  formatNodeChange,
} from '../workflowStudioChanges'
import { WorkflowPublishReviewDialogChangeCount } from './WorkflowPublishReviewDialogChangeCount'
import { WorkflowPublishReviewDialogChangeList } from './WorkflowPublishReviewDialogChangeList'
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
      viewModel.metadataChanges.length > 0 ||
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
        <WorkflowPublishReviewDialogChangeCount
          count={viewModel.metadataChanges.length}
          label="元数据变更"
        />
        {viewModel.riskLevel !== 'none' && (
          <WorkflowPublishReviewDialogRiskChip
            severity={viewModel.riskLevel as 'info' | 'warning' | 'breaking'}
          />
        )}
      </div>

      <WorkflowPublishReviewDialogChangeList
        title="节点变更"
        items={viewModel.nodeChanges.map((change) => ({
          key: `node-${change.type}-${change.nodeKey}`,
          text: formatNodeChange(change),
          severity: change.severity as 'info' | 'warning' | 'breaking',
        }))}
      />

      <WorkflowPublishReviewDialogChangeList
        title="边变更"
        items={viewModel.edgeChanges.map((change) => ({
          key: `edge-${change.type}-${change.source}-${change.target}`,
          text: formatEdgeChange(change),
          severity: change.severity as 'info' | 'warning' | 'breaking',
        }))}
      />

      <WorkflowPublishReviewDialogChangeList
        title="Intake 变更"
        items={viewModel.intakeChanges.map((change) => ({
          key: `intake-${change.type}-${change.modeKey}-${change.fieldKey ?? ''}`,
          text: formatIntakeChange(change),
          severity: change.severity as 'info' | 'warning' | 'breaking',
        }))}
      />

      <WorkflowPublishReviewDialogChangeList
        title="元数据变更"
        items={viewModel.metadataChanges.map((change) => ({
          key: `metadata-${change.field}`,
          text: formatMetadataChange(change),
          severity: change.severity as 'info' | 'warning' | 'breaking',
        }))}
      />

      <WorkflowPublishReviewDialogChangeList
        title="风险提示"
        items={viewModel.riskFlags.map((flag) => ({
          key: `risk-${flag.code}`,
          text: flag.message,
          severity: flag.severity as 'info' | 'warning' | 'breaking',
        }))}
      />
    </>
  )
}
