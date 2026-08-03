import { TextField } from '@mui/material'
import { useState } from 'react'
import { useDebouncedCallback } from '../../hooks/useDebouncedCallback'
import styles from './JobFilterBar.module.css'
import filterStyles from '../FilterControls.module.css'

export function JobSearchFilter(props: {
  value: string
  onChange: (value: string) => void
}) {
  const [draft, setDraft] = useState(() => ({
    committed: props.value,
    value: props.value,
  }))
  const value = draft.committed === props.value ? draft.value : props.value
  const debouncedChange = useDebouncedCallback(props.onChange, 250)
  return (
    <TextField
      type="search"
      size="small"
      placeholder="搜索 ID / 标题 / 批次"
      value={value}
      onChange={(event) => {
        const next = event.target.value
        setDraft({ committed: props.value, value: next })
        debouncedChange(next)
      }}
      className={`${styles.search} ${filterStyles.filterControl}`}
      InputProps={{ className: filterStyles.filterPlaceholder }}
    />
  )
}
