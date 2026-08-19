import { Button } from '@mui/material'
import type { JobSummary, WorkflowDefinitionRecord } from '../../types'
import type { FailureCategory } from '../../types/failureTypes'
import type { WorkflowNodesByKey } from '../JobRerunDialog'
import type { FailureCategoryContext } from '../JobRerunDialog/useFailureCategories'
import { JobActionBarActions } from './JobActionBarActions'
import styles from './JobActionBar.module.css'

export {
  canRerunJob,
  canPackageJob,
  canDeleteJob,
  canContinueJob,
} from '../jobActionEligibility'

export type JobActionBarFilter = {
  key: string
  label: string
  onClick: () => void
}

export type JobActionBarProps = {
  jobs: JobSummary[]
  selectedCount?: number
  workspaceId?: string
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  mode?: 'batch' | 'single'
  loading?: boolean
  filters?: JobActionBarFilter[]
  onExitSelectMode?: () => void
  failureContext?: FailureCategoryContext
  /**
   * Selection count when the selection is filter-based ('allMatching'
   * mode); null/undefined means an explicit id selection. Filter-based
   * selections disable actions that need per-job client-side eligibility.
   */
  allMatchingCount?: number | null
  onRerun: (
    nodeKey: string | null,
    fromFailedNode?: boolean,
    jobIds?: string[],
    failureCategory?: FailureCategory
  ) => void
  onRunTo?: (targetKey: string, startKey?: string) => void | Promise<void>
  onContinue?: () => void | Promise<void>
  onPackage: () => void | Promise<void>
  onClearPacked?: () => void | Promise<void>
  onDelete: () => void | Promise<void>
  onPause?: () => void | Promise<void>
  onResume?: () => void | Promise<void>
  onUpgradeWorkflow?: (jobIds?: string[]) => void | Promise<void>
  itemLabel?: string
}

export function JobActionBar(props: JobActionBarProps) {
  const { jobs, selectedCount, mode, filters } = props
  const isBatch = (mode ?? (jobs.length > 1 ? 'batch' : 'single')) === 'batch'
  const count = selectedCount ?? jobs.length

  return (
    <div className={styles.actionBar} data-testid="job-action-bar">
      {isBatch && (
        <div className={styles.batchHeader}>
          <span className={styles.count}>已选择 {count} 项</span>
          {filters && filters.length > 0 && (
            <div className={styles.filters}>
              {filters.map((filter) => (
                <Button
                  key={filter.key}
                  variant="text"
                  onClick={filter.onClick}
                >
                  {filter.label}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}

      <JobActionBarActions {...props} isBatch={isBatch} />
    </div>
  )
}
