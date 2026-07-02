import { groupValidationErrors } from './workflowStudioModel'
import styles from './WorkflowValidationPanel.module.css'

type Props = { message: string; errors: string[] }

function ErrorGroup({ title, errors }: { title: string; errors: string[] }) {
  if (errors.length === 0) return null
  return (
    <div className={styles.group}>
      <h3 className={styles.groupTitle}>{title}</h3>
      <ul className={styles.list}>
        {errors.map((error) => (
          <li key={error} className={styles.listItem}>
            {error}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function WorkflowValidationPanel({ message, errors }: Props) {
  const groups = groupValidationErrors(errors)
  if (!message && errors.length === 0) return null
  const messageClass = `${styles.message} ${message.includes('成功') || message.includes('通过') ? styles.success : styles.error}`
  return (
    <section aria-label="Workflow validation" className={styles.panel}>
      {message && <p className={messageClass}>{message}</p>}
      <ErrorGroup title="结构校验" errors={groups.structural} />
      <ErrorGroup title="执行器绑定" errors={groups.executor} />
    </section>
  )
}
