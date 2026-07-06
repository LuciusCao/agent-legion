import { useMemo } from 'react'
import {
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Chip,
} from '@mui/material'
import type { JobFilterConfig } from '../stores/job/state'
import type { FilterCounts } from '../stores/job/selectors'
import type { JobSummary } from '../jobTypes'
import type { WorkflowDefinitionRecord } from '../types'
import { JOB_STATUS_LABELS } from '../labels'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import styles from './JobFilterBar.module.css'

const STATUS_OPTIONS: JobFilterConfig['status'][] = [
  'all',
  'pending',
  'running',
  'completed',
  'failed',
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

  const versionOptions = useMemo(() => {
    const versions = new Set<number>()
    for (const job of jobs) {
      if (job.workflow_version !== null && job.workflow_version !== undefined) {
        versions.add(job.workflow_version)
      }
    }
    return Array.from(versions).sort((a, b) => b - a)
  }, [jobs])

  const nodeOptions = useMemo(() => {
    const defined = new Map<string, string>()
    for (const node of workflowDefinition?.nodes ?? []) {
      defined.set(node.key, node.label)
    }
    const seen = new Set<string>()
    for (const job of jobs) {
      if (job.active_node_key) seen.add(job.active_node_key)
      for (const node of job.node_summaries ?? []) {
        seen.add(node.node_key)
      }
    }
    const options: { key: string; label: string }[] = []
    for (const [key, label] of defined) {
      options.push({ key, label })
      seen.delete(key)
    }
    for (const key of seen) {
      options.push({ key, label: key })
    }
    return options
  }, [workflowDefinition, jobs])

  const activeFilters = [
    filterConfig.status !== 'all' && {
      label: `状态: ${JOB_STATUS_LABELS[filterConfig.status]}`,
      onDelete: () => onChange({ status: 'all' }),
    },
    filterConfig.workflowVersion !== null && {
      label: `版本: v${filterConfig.workflowVersion}`,
      onDelete: () => onChange({ workflowVersion: null }),
    },
    filterConfig.activeNodeKey !== null && {
      label: `节点: ${nodeOptions.find((n) => n.key === filterConfig.activeNodeKey)?.label ?? filterConfig.activeNodeKey}`,
      onDelete: () => onChange({ activeNodeKey: null }),
    },
    filterConfig.search && {
      label: `搜索: "${filterConfig.search}"`,
      onDelete: () => onChange({ search: '' }),
    },
  ].filter(Boolean) as { label: string; onDelete: () => void }[]

  return (
    <div className={styles.panel}>
      <div className={styles.row}>
        <FormControl size="small" className={styles.control}>
          <InputLabel id="job-status-filter-label">状态</InputLabel>
          <Select
            labelId="job-status-filter-label"
            value={filterConfig.status}
            label="状态"
            onChange={(e) =>
              onChange({ status: e.target.value as JobFilterConfig['status'] })
            }
          >
            {STATUS_OPTIONS.map((status) => (
              <MenuItem key={status} value={status}>
                {status === 'all' ? '全部状态' : JOB_STATUS_LABELS[status]} (
                {counts.status[status] ?? 0})
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" className={styles.control}>
          <InputLabel id="job-version-filter-label">Workflow 版本</InputLabel>
          <Select
            labelId="job-version-filter-label"
            value={
              filterConfig.workflowVersion === null
                ? ''
                : String(filterConfig.workflowVersion)
            }
            label="Workflow 版本"
            onChange={(e) => {
              const value = e.target.value
              onChange({
                workflowVersion: value === '' ? null : Number(value),
              })
            }}
          >
            <MenuItem value="">
              全部版本 (
              {Object.values(counts.workflowVersion).reduce((a, b) => a + b, 0)}
              )
            </MenuItem>
            {versionOptions.map((version) => (
              <MenuItem key={version} value={String(version)}>
                v{version} ({counts.workflowVersion[String(version)] ?? 0})
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" className={styles.control}>
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
          >
            <MenuItem value="">
              全部节点 (
              {Object.values(counts.activeNodeKey).reduce((a, b) => a + b, 0)})
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
          className={styles.search}
        />
      </div>

      {activeFilters.length > 0 && (
        <div className={styles.chips}>
          {activeFilters.map((filter) => (
            <Chip
              key={filter.label}
              label={filter.label}
              onDelete={filter.onDelete}
              size="small"
            />
          ))}
        </div>
      )}
    </div>
  )
}
