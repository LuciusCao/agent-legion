import { FormControl, InputLabel, MenuItem, Select } from '@mui/material'
import type { JobFilterConfig } from '../../stores/jobStore'
import filterStyles from '../FilterControls.module.css'

const PAUSED_OPTIONS: { value: string; label: string }[] = [
  { value: 'true', label: '已暂停' },
  { value: 'false', label: '未暂停' },
]

export interface JobPausedFilterProps {
  value: JobFilterConfig['paused']
  className?: string
  onChange: (paused: JobFilterConfig['paused']) => void
}

export function JobPausedFilter({
  value,
  className,
  onChange,
}: JobPausedFilterProps) {
  return (
    <FormControl
      size="small"
      className={`${filterStyles.filterControl} ${className ?? ''}`}
    >
      <InputLabel id="job-paused-filter-label">暂停</InputLabel>
      <Select
        labelId="job-paused-filter-label"
        value={value === null ? '' : String(value)}
        label="暂停"
        onChange={(e) => {
          const selected = e.target.value as string
          onChange(selected === '' ? null : selected === 'true')
        }}
        MenuProps={{ PaperProps: { className: filterStyles.filterMenu } }}
      >
        <MenuItem value="">全部</MenuItem>
        {PAUSED_OPTIONS.map((option) => (
          <MenuItem key={option.value} value={option.value}>
            {option.label}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  )
}
