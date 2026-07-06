import {
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material'
import type { JobFilterConfig, JobStatus } from '../stores/job/state'
import type { FilterCounts } from '../stores/job/selectors'
import type { JobSummary } from '../jobTypes'
import type { WorkflowDefinitionRecord } from '../types'
import { JOB_STATUS_LABELS } from '../labels'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import { WorkflowVersionFilter } from './WorkflowVersionFilter'
import { JobFilterBarChips } from './JobFilterBarChips'
import { useJobFilterActiveFilters } from './useJobFilterActiveFilters'
import { useJobFilterNodeOptions } from './useJobFilterNodeOptions'
import styles from './JobFilterBar.module.css'
import filterStyles from './FilterControls.module.css'

const STATUS_OPTIONS: JobStatus[] = [
  'pending',
  'running',
  'completed',
  'failed',
  'paused',
]

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
        <FormControl
          size="small"
          className={`${styles.control} ${filterStyles.filterControl}`}
        >
          <InputLabel id="job-status-filter-label">状态</InputLabel>
          <Select
            labelId="job-status-filter-label"
            value={filterConfig.status ?? ''}
            label="状态"
            onChange={(e) => {
              const value = e.target.value as string
              onChange({
                status:
                  value === '' ? null : (value as JobFilterConfig['status']),
              })
            }}
            MenuProps={{ PaperProps: { className: filterStyles.filterMenu } }}
            renderValue={(value) => {
              const status = value as string
              if (status === '') {
                return (
                  <span className={filterStyles.filterPlaceholder}>
                    全部状态 ({counts.status.all ?? 0})
                  </span>
                )
              }
              const statusKey = status as JobStatus
              const count = counts.status[statusKey] ?? 0
              return (
                <span>
                  {JOB_STATUS_LABELS[statusKey]} ({count})
                </span>
              )
            }}
          >
            <MenuItem value="">全部状态 ({counts.status.all ?? 0})</MenuItem>
            {STATUS_OPTIONS.map((status) => (
              <MenuItem key={status} value={status}>
                {JOB_STATUS_LABELS[status]} ({counts.status[status] ?? 0})
              </MenuItem>
            ))}
          </Select>
        </FormControl>

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
