import { Chip } from '@mui/material'
import {
  FAILURE_CATEGORY_HINTS,
  FAILURE_CATEGORY_LABELS,
  FAILURE_CATEGORY_ORDER,
} from './failureCategoryCounts'
import type { FailureCategoryState } from './useFailureCategories'
import styles from './JobRerunDialog.module.css'

export function JobRerunFailureCategoryRow({
  failure,
}: {
  failure: FailureCategoryState
}) {
  return (
    <div className={styles.categoryRow}>
      <Chip
        data-testid="rerun-category-all"
        label="全部失败"
        color="error"
        variant={failure.selection === 'all' ? 'filled' : 'outlined'}
        onClick={() => failure.setSelection('all')}
      />
      {FAILURE_CATEGORY_ORDER.map((category) => {
        const count = failure.counts?.[category]
        return (
          <Chip
            key={category}
            data-testid={`rerun-category-${category}`}
            label={
              count == null
                ? FAILURE_CATEGORY_LABELS[category]
                : `${FAILURE_CATEGORY_LABELS[category]} (${count})`
            }
            title={FAILURE_CATEGORY_HINTS[category]}
            variant={failure.selection === category ? 'filled' : 'outlined'}
            disabled={count === 0}
            onClick={() => failure.setSelection(category)}
          />
        )
      })}
    </div>
  )
}
