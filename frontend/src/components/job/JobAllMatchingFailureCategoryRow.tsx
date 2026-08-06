import { Box, Chip, Typography } from '@mui/material'
import {
  FAILURE_CATEGORY_HINTS,
  FAILURE_CATEGORY_LABELS,
  FAILURE_CATEGORY_ORDER,
  type FailureCategorySelection,
} from '../JobRerunDialog/failureCategoryCounts'

export type JobAllMatchingFailureCategoryRowProps = {
  /** Node mode mutes the category chips' filled state. */
  active: boolean
  selection: FailureCategorySelection
  onSelect: (selection: FailureCategorySelection) => void
}

/** Failure-category rerun chips for the allMatching dialog. */
export function JobAllMatchingFailureCategoryRow({
  active,
  selection,
  onSelect,
}: JobAllMatchingFailureCategoryRowProps) {
  return (
    <>
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
        按失败类别重跑
      </Typography>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
        <Chip
          data-testid="rerun-chip-all-failed"
          label="全部失败"
          color="error"
          variant={active && selection === 'all' ? 'filled' : 'outlined'}
          onClick={() => onSelect('all')}
        />
        {FAILURE_CATEGORY_ORDER.map((category) => (
          <Chip
            key={category}
            data-testid={`rerun-chip-${category}`}
            label={FAILURE_CATEGORY_LABELS[category]}
            title={FAILURE_CATEGORY_HINTS[category]}
            variant={active && selection === category ? 'filled' : 'outlined'}
            onClick={() => onSelect(category)}
          />
        ))}
      </Box>
    </>
  )
}
