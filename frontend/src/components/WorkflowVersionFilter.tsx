import { useMemo } from 'react'
import { Select, MenuItem, FormControl, InputLabel } from '@mui/material'
import type { JobFilterConfig } from '../stores/job/state'
import type { JobSummary } from '../jobTypes'
import styles from './JobFilterBar.module.css'

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
  const { versionOptions, hasMissingVersion } = useMemo(() => {
    const versions = new Set<number>()
    let hasMissingVersion = false
    for (const job of jobs) {
      if (job.workflow_version !== null && job.workflow_version !== undefined) {
        versions.add(job.workflow_version)
      } else {
        hasMissingVersion = true
      }
    }
    return {
      versionOptions: Array.from(versions).sort((a, b) => b - a),
      hasMissingVersion,
    }
  }, [jobs])

  return (
    <FormControl size="small" className={styles.control}>
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
