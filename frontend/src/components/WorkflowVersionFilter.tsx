import { Select, MenuItem, FormControl, InputLabel } from '@mui/material'
import type { JobFilterConfig } from '../stores/job/state'
import type { JobSummary } from '../jobTypes'
import filterStyles from './FilterControls.module.css'
import { useWorkflowVersionOptions } from './useWorkflowVersionOptions'

export interface WorkflowVersionFilterProps {
  value: JobFilterConfig['workflowVersion']
  counts: Record<string, number>
  jobs: JobSummary[]
  onChange: (workflowVersion: JobFilterConfig['workflowVersion']) => void
}

export function WorkflowVersionFilter({
  value,
  counts,
  jobs,
  onChange,
}: WorkflowVersionFilterProps) {
  const { versionOptions, hasMissingVersion } = useWorkflowVersionOptions(jobs)

  return (
    <FormControl size="small" className={filterStyles.filterControl}>
      <InputLabel id="job-version-filter-label">Workflow 版本</InputLabel>
      <Select
        labelId="job-version-filter-label"
        value={value === null ? '' : String(value)}
        label="Workflow 版本"
        onChange={(e) => {
          const selected = e.target.value
          onChange(
            selected === ''
              ? null
              : selected === 'none'
                ? 'none'
                : Number(selected)
          )
        }}
        MenuProps={{ PaperProps: { className: filterStyles.filterMenu } }}
        renderValue={(selected) => {
          const key = selected as string
          if (key === '') {
            return (
              <span className={filterStyles.filterPlaceholder}>
                全部版本 ({counts.all ?? 0})
              </span>
            )
          }
          if (key === 'none') {
            return <span>未指定版本 ({counts.none ?? 0})</span>
          }
          return (
            <span>
              v{key} ({counts[key] ?? 0})
            </span>
          )
        }}
      >
        <MenuItem value="">全部版本 ({counts.all ?? 0})</MenuItem>
        {hasMissingVersion && (
          <MenuItem value="none">未指定版本 ({counts.none ?? 0})</MenuItem>
        )}
        {versionOptions.map((version) => (
          <MenuItem key={version} value={String(version)}>
            v{version} ({counts[String(version)] ?? 0})
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  )
}
