import { Button } from '@mui/material'
import type { JobSummary } from '../../types'
import { JobActionBarUpgrade } from './JobActionBarUpgrade'

export interface JobActionBarBatchButtonsProps {
  allMatching: boolean
  allMatchingCount: number
  jobs: JobSummary[]
  itemLabel: string
  loading: boolean
  onOpenUpgrade: () => void
  onUpgradeWorkflow?: (jobIds?: string[]) => void | Promise<void>
  onPause?: () => void | Promise<void>
  onResume?: () => void | Promise<void>
}

/** Batch-mode leading actions: workflow upgrade plus execution pause/resume. */
export function JobActionBarBatchButtons({
  allMatching,
  allMatchingCount,
  jobs,
  itemLabel,
  loading,
  onOpenUpgrade,
  onUpgradeWorkflow,
  onPause,
  onResume,
}: JobActionBarBatchButtonsProps) {
  const pauseDisabled =
    loading || (allMatching ? allMatchingCount : jobs.length) === 0
  return (
    <>
      {allMatching ? (
        <Button
          variant="outlined"
          onClick={onOpenUpgrade}
          disabled={loading || allMatchingCount === 0 || !onUpgradeWorkflow}
        >
          升级 workflow
        </Button>
      ) : (
        <JobActionBarUpgrade
          jobs={jobs}
          itemLabel={itemLabel}
          loading={loading}
          onUpgradeWorkflow={onUpgradeWorkflow}
        />
      )}
      {onPause && (
        <Button variant="outlined" onClick={onPause} disabled={pauseDisabled}>
          暂停
        </Button>
      )}
      {onResume && (
        <Button variant="outlined" onClick={onResume} disabled={pauseDisabled}>
          恢复
        </Button>
      )}
    </>
  )
}
