import { useMemo } from 'react'
import { useJobStore } from '../../stores/jobStore'
import type { JobFilterConfig } from '../../stores/job/state'
import type { FilterCounts } from '../../stores/job/selectors'
import type { WorkflowDefinitionRecord } from '../../types'
import {
  makeSelectNodeOptions,
  selectWorkflowVersionOptions,
} from '../../stores/job/selectors'
import { WorkflowVersionFilter } from '../WorkflowVersionFilter'
import { JobFilterBarChips } from './JobFilterBarChips'
import { JobStatusFilter } from './JobStatusFilter'
import { JobNodeFilter } from './JobNodeFilter'
import { JobSearchFilter } from './JobSearchFilter'
import { useJobFilterActiveFilters } from '../useJobFilterActiveFilters'
import styles from './JobFilterBar.module.css'

export interface JobFilterBarProps {
  filterConfig: JobFilterConfig
  counts: FilterCounts
  workflowDefinition: WorkflowDefinitionRecord | null
  onChange: (config: Partial<JobFilterConfig>) => void
}

export function JobFilterBar({
  filterConfig,
  counts,
  workflowDefinition,
  onChange,
}: JobFilterBarProps) {
  const selectNodeOptions = useMemo(
    () => makeSelectNodeOptions(workflowDefinition),
    [workflowDefinition]
  )
  const nodeOptions = useJobStore(selectNodeOptions)
  const versionOptions = useJobStore(selectWorkflowVersionOptions)

  const activeFilters = useJobFilterActiveFilters(
    filterConfig,
    nodeOptions,
    onChange
  )

  return (
    <div className={styles.panel}>
      <div className={styles.row}>
        <JobStatusFilter
          value={filterConfig.status}
          counts={counts.status}
          className={styles.control}
          onChange={(status) => onChange({ status })}
        />

        <div className={styles.workflowVersionControl}>
          <WorkflowVersionFilter
            value={filterConfig.workflowVersion}
            counts={counts.workflowVersion}
            versionOptions={versionOptions}
            onChange={(workflowVersion) => onChange({ workflowVersion })}
          />
        </div>

        <JobNodeFilter
          value={filterConfig.activeNodeKey}
          counts={counts.activeNodeKey}
          options={nodeOptions}
          className={styles.control}
          onChange={(activeNodeKey) => onChange({ activeNodeKey })}
        />

        <JobSearchFilter
          value={filterConfig.search}
          onChange={(search) => onChange({ search })}
        />
      </div>

      <JobFilterBarChips filters={activeFilters} />
    </div>
  )
}
