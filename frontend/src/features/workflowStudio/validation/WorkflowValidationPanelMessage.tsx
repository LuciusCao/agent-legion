import styles from './WorkflowValidationPanelMessage.module.css'

type Props = {
  message: string
}

export function WorkflowValidationPanelMessage({ message }: Props) {
  const isSuccess = message.includes('成功') || message.includes('通过')
  return (
    <p
      className={`${styles.message} ${isSuccess ? styles.success : styles.error}`}
    >
      {message}
    </p>
  )
}
