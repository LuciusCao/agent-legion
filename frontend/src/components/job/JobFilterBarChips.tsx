import { Chip } from '@mui/material'
import styles from './JobFilterBar.module.css'

export interface JobFilterBarChipsProps {
  filters: { label: string; onDelete: () => void }[]
}

export function JobFilterBarChips({ filters }: JobFilterBarChipsProps) {
  if (filters.length === 0) {
    return null
  }
  return (
    <div className={styles.chips}>
      {filters.map((filter) => (
        <Chip
          key={filter.label}
          label={filter.label}
          onDelete={filter.onDelete}
          size="small"
        />
      ))}
    </div>
  )
}
