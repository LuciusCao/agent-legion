import { MaterialIcon } from './MaterialIcon'
import styles from './EmptyStateGuide.module.css'

export interface EmptyStateGuideProps {
  steps: Array<{
    icon: string
    title: string
    description: string
    unlocked: boolean
    actionLabel: string
    onAction: () => void
  }>
}

export function EmptyStateGuide({ steps }: EmptyStateGuideProps) {
  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <MaterialIcon className={styles.rocket} name="rocket_launch" />
        <h2 className={styles.title}>开始使用 Workspace</h2>
        <p className={styles.subtitle}>按以下步骤配置并启动你的第一个任务</p>
      </div>

      <div className={styles.steps}>
        {steps.map((step, idx) => (
          <div
            key={idx}
            data-step={idx}
            className={`${styles.stepCard} ${
              step.unlocked ? styles.unlocked : styles.locked
            }`}
          >
            <div className={styles.stepIcon}>
              <MaterialIcon name={step.icon} />
            </div>
            <div className={styles.stepBody}>
              <h3 className={styles.stepTitle}>{step.title}</h3>
              <p className={styles.stepDesc}>{step.description}</p>
              <button
                type="button"
                className={styles.stepAction}
                disabled={!step.unlocked}
                onClick={step.onAction}
              >
                {step.actionLabel}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
