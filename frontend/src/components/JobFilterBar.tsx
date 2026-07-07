import {
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material'
import type { JobFilterConfig } from '../stores/job/state'
import type { FilterCounts } from '../stores/job/selectors'
import type { JobSummary } from '../jobTypes'
import type { WorkflowDefinitionRecord } from '../types'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import { WorkflowVersionFilter } from './WorkflowVersionFilter'
import { JobFilterBarChips } from './JobFilterBarChips'
import { useJobFilterActiveFilters } from './useJobFilterActiveFilters'
import { useJobFilterNodeOptions } from './useJobFilterNodeOptions'
import styles from './JobFilterBar.module.css'
import filterStyles from './FilterControls.module.css'

export interface JobFilterBarProps {
  filterConfig: JobFilterConfig
  counts: FilterCounts
  workflowDefinition: WorkflowDefinitionRecord | null
  jobs: JobSummary[]
  onChange: (config: Partial<JobFilterConfig>) => void
}

export function JobFilterBar({
  filterConfig,
  counts,
  workflowDefinition,
  jobs,
  onChange,
}: JobFilterBarProps) {
  const debouncedSearch = useDebouncedCallback(
    (value: string) => onChange({ search: value }),
    250
  )

  const nodeOptions = useJobFilterNodeOptions(workflowDefinition, jobs)

  const activeFilters = useJobFilterActiveFilters(
    filterConfig,
    nodeOptions,
    onChange
  )

  return (
    <div className={styles.panel}>
      <div className={styles.row}>
        <WorkflowVersionFilter
          value={filterConfig.workflowVersion}
          counts={counts.workflowVersion}
          jobs={jobs}
          onChange={(workflowVersion) => onChange({ workflowVersion })}
        />

        <FormControl
          size="small"
          className={`${styles.control} ${filterStyles.filterControl}`}
        >
          <InputLabel id="job-node-filter-label">当前运行节点</InputLabel>
          <Select
            labelId="job-node-filter-label"
            value={filterConfig.activeNodeKey ?? ''}
            label="当前运行节点"
            onChange={(e) =>
              onChange({
                activeNodeKey: e.target.value === '' ? null : e.target.value,
              })
            }
            MenuProps={{ PaperProps: { className: filterStyles.filterMenu } }}
            renderValue={(value) => {
              const key = value as string
              if (key === '') {
                return (
                  <span className={filterStyles.filterPlaceholder}>
                    全部节点 ({counts.activeNodeKey.all ?? 0})
                  </span>
                )
              }
              const node = nodeOptions.find((n) => n.key === key)
              return (
                <span>
                  {node?.label ?? key} ({counts.activeNodeKey[key] ?? 0})
                </span>
              )
            }}
          >
            <MenuItem value="">
              全部节点 ({counts.activeNodeKey.all ?? 0})
            </MenuItem>
            {nodeOptions.map((node) => (
              <MenuItem key={node.key} value={node.key}>
                {node.label} ({counts.activeNodeKey[node.key] ?? 0})
              </MenuItem>
            ))}
          </Select>
        </FormControl>

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
