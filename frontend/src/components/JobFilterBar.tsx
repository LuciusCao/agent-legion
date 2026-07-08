import { useMemo } from 'react'
import { TextField } from '@mui/material'
import { useJobStore } from '../stores/jobStore'
import type { JobFilterConfig } from '../stores/job/state'
import type { FilterCounts } from '../stores/job/selectors'
import type { WorkflowDefinitionRecord } from '../types'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import {
  makeSelectNodeOptions,
  selectWorkflowVersionOptions,
} from '../stores/job/selectors'
import { WorkflowVersionFilter } from './WorkflowVersionFilter'
import { JobFilterBarChips } from './JobFilterBarChips'
import { JobStatusFilter } from './JobStatusFilter'
import { JobNodeFilter } from './JobNodeFilter'
import { useJobFilterActiveFilters } from './useJobFilterActiveFilters'
import styles from './JobFilterBar.module.css'
import filterStyles from './FilterControls.module.css'

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
  const debouncedSearch = useDebouncedCallback(
    (value: string) => onChange({ search: value }),
    250
  )

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

        <TextField
          type="search"
          size="small"
          placeholder="搜索 ID / 标题 / 批次"
          defaultValue={filterConfig.search}
          onChange={(e) => debouncedSearch(e.target.value)}
          className={`${styles.search} ${filterStyles.filterControl}`}
          InputProps={{ className: filterStyles.filterPlaceholder }}
        />
      </div>

      <JobFilterBarChips filters={activeFilters} />
    </div>
  )
}
