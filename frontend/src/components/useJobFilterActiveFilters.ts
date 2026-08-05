import { useMemo } from 'react'
import type { JobFilterConfig, JobFilterNodeOption } from '../stores/jobStore'
import { JOB_STATUS_LABELS } from '../labels'

export interface JobFilterActiveFilter {
  label: string
  onDelete: () => void
}

export function useJobFilterActiveFilters(
  filterConfig: JobFilterConfig,
  nodeOptions: JobFilterNodeOption[],
  onChange: (config: Partial<JobFilterConfig>) => void
): JobFilterActiveFilter[] {
  return useMemo(
    () =>
      [
        filterConfig.status !== null && {
          label: `状态: ${JOB_STATUS_LABELS[filterConfig.status]}`,
          onDelete: () => onChange({ status: null }),
        },
        filterConfig.workflowVersion !== null && {
          label:
            filterConfig.workflowVersion === 'none'
              ? '版本: 未指定版本'
              : `版本: v${filterConfig.workflowVersion}`,
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
      ].filter(Boolean) as JobFilterActiveFilter[],
    [filterConfig, nodeOptions, onChange]
  )
}
