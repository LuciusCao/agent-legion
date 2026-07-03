import type { WorkflowDefinitionRecord } from '../../types'
import styles from './WorkflowStudioSummaryBar.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord | null
}

export function WorkflowStudioSummaryBarTitle({ workflow }: Props) {
  return (
    <div className={styles.titleBlock}>
      <h1 className={styles.title}>{workflow?.label ?? '工作流'}</h1>
      <p className={styles.subtitle}>{workflow?.key ?? '未加载'}</p>
    </div>
  )
}
