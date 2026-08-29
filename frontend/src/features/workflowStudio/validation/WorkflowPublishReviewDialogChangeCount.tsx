import styles from './WorkflowPublishReviewDialog.module.css'

type Props = {
  count: number
  label: string
}

export function WorkflowPublishReviewDialogChangeCount({
  count,
  label,
}: Props) {
  if (count === 0) return null
  return (
    <span className={styles.count}>
      {label}: {count}
    </span>
  )
}
