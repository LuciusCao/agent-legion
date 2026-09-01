import { FormControl, InputLabel, MenuItem, Select } from '@mui/material'
import type {
  JobFilterConfig,
  JobStatus,
  FilterCounts,
} from '../../stores/jobStore'
import { JOB_STATUS_LABELS } from '../../labels'
import filterStyles from '../FilterControls.module.css'

const STATUS_OPTIONS: JobStatus[] = [
  'pending',
  'running',
  'awaiting_approval',
  'completed',
  'failed',
  'paused',
]

export interface JobStatusFilterProps {
  value: JobFilterConfig['status']
  counts: FilterCounts['status']
  className?: string
  onChange: (status: JobFilterConfig['status']) => void
}

export function JobStatusFilter({
  value,
  counts,
  className,
  onChange,
}: JobStatusFilterProps) {
  return (
    <FormControl
      size="small"
      className={`${filterStyles.filterControl} ${className ?? ''}`}
    >
      <InputLabel id="job-status-filter-label">状态</InputLabel>
      <Select
        labelId="job-status-filter-label"
        value={value ?? ''}
        label="状态"
        onChange={(e) => {
          const selected = e.target.value as string
          onChange(
            selected === '' ? null : (selected as JobFilterConfig['status'])
          )
        }}
        MenuProps={{ PaperProps: { className: filterStyles.filterMenu } }}
        renderValue={(selected) => {
          const status = selected as string
          if (status === '') {
            return (
              <span className={filterStyles.filterPlaceholder}>
                全部状态 ({counts.all ?? 0})
              </span>
            )
          }
          const statusKey = status as JobStatus
          return (
            <span>
              {JOB_STATUS_LABELS[statusKey]} ({counts[statusKey] ?? 0})
            </span>
          )
        }}
      >
        <MenuItem value="">全部状态 ({counts.all ?? 0})</MenuItem>
        {STATUS_OPTIONS.map((status) => (
          <MenuItem key={status} value={status}>
            {JOB_STATUS_LABELS[status]} ({counts[status] ?? 0})
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  )
}
