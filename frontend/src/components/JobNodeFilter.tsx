import { FormControl, InputLabel, MenuItem, Select } from '@mui/material'
import type { JobFilterConfig } from '../stores/job/state'
import type { FilterCounts } from '../stores/job/selectors'
import filterStyles from './FilterControls.module.css'

export interface NodeOption {
  key: string
  label: string
}

export interface JobNodeFilterProps {
  value: JobFilterConfig['activeNodeKey']
  counts: FilterCounts['activeNodeKey']
  options: NodeOption[]
  className?: string
  onChange: (activeNodeKey: JobFilterConfig['activeNodeKey']) => void
}

export function JobNodeFilter({
  value,
  counts,
  options,
  className,
  onChange,
}: JobNodeFilterProps) {
  return (
    <FormControl
      size="small"
      className={`${filterStyles.filterControl} ${className ?? ''}`}
    >
      <InputLabel id="job-node-filter-label">当前运行节点</InputLabel>
      <Select
        labelId="job-node-filter-label"
        value={value ?? ''}
        label="当前运行节点"
        onChange={(e) =>
          onChange(e.target.value === '' ? null : e.target.value)
        }
        MenuProps={{ PaperProps: { className: filterStyles.filterMenu } }}
        renderValue={(selected) => {
          const key = selected as string
          if (key === '') {
            return (
              <span className={filterStyles.filterPlaceholder}>
                全部节点 ({counts.all ?? 0})
              </span>
            )
          }
          const node = options.find((n) => n.key === key)
          return (
            <span>
              {node?.label ?? key} ({counts[key] ?? 0})
            </span>
          )
        }}
      >
        <MenuItem value="">全部节点 ({counts.all ?? 0})</MenuItem>
        {options.map((node) => (
          <MenuItem key={node.key} value={node.key}>
            {node.label} ({counts[node.key] ?? 0})
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  )
}
