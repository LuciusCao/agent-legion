import styles from './SettingsCard.module.css'

interface SettingsCardProps {
  icon: string
  title: string
  status?: React.ReactNode
  children: React.ReactNode
}

export function SettingsCard({
  icon,
  title,
  status,
  children,
}: SettingsCardProps) {
  return (
    <div className={`card-outlined ${styles.card}`}>
      <div className={styles.header}>
        <span className={styles.icon}>
          <md-icon>{icon}</md-icon>
        </span>
        <span className={styles.title}>{title}</span>
        {status && <span className={styles.status}>{status}</span>}
      </div>
      <div className={styles.body}>{children}</div>
    </div>
  )
}
