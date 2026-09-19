import type { JobSummary } from '../../types'
import { LabeledIconButton } from '../LabeledIconButton'

export function JobWorkflowUpgradeButton({
  jobs,
  loading,
  onUpgradeWorkflow,
}: {
  jobs: JobSummary[]
  loading: boolean
  onUpgradeWorkflow: () => void | Promise<void>
}) {
  const disabled =
    jobs.length !== 1 ||
    loading ||
    !jobs[0].is_workflow_outdated ||
    jobs[0].status === 'running'

  return (
    <LabeledIconButton
      icon="arrow_circle_up"
      label="升级"
      ariaLabel="升级 workflow"
      disabled={disabled}
      onClick={onUpgradeWorkflow}
    />
  )
}
