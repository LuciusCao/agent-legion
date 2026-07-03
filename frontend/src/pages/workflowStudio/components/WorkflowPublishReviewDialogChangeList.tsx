import { WorkflowPublishReviewDialogRiskChip } from './WorkflowPublishReviewDialogRiskChip'
import styles from './WorkflowPublishReviewDialog.module.css'

type ChangeItem = {
  key: string
  text: string
  severity: 'info' | 'warning' | 'breaking'
}

type Props = {
  title: string
  items: ChangeItem[]
}

export function WorkflowPublishReviewDialogChangeList({ title, items }: Props) {
  if (items.length === 0) return null
  return (
    <section className={styles.group}>
      <h3 className={styles.groupTitle}>{title}</h3>
      <ul className={styles.list}>
        {items.map((item) => (
          <li key={item.key} className={styles.item}>
            <span className={styles.itemText}>{item.text}</span>
            <WorkflowPublishReviewDialogRiskChip severity={item.severity} />
          </li>
        ))}
      </ul>
    </section>
  )
}
