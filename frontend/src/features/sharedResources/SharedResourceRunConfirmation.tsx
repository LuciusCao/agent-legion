import { Button } from '@mui/material'
import type { useRunSharedResources } from './useRunSharedResources'
import styles from './SharedResources.module.css'

export function SharedResourceRunConfirmation({
  state,
}: {
  state: ReturnType<typeof useRunSharedResources>
}) {
  if (state.isLoading && !state.hasResources) {
    return <p className={styles.help}>正在确认共享资源…</p>
  }
  if (state.isError) {
    return (
      <div className={`${styles.notice} ${styles.warningNotice}`} role="alert">
        无法确认本次使用的共享资源，请重试后再创建运行。
        <Button size="small" onClick={state.refetch}>
          重新确认
        </Button>
      </div>
    )
  }
  if (!state.hasResources) return null
  return (
    <section className={styles.usage} aria-label="本次运行使用的共享资源">
      <h3 className={styles.usageTitle}>本次运行使用的共享资源</h3>
      <div className={styles.usageItems}>
        {state.resources.map((resource) => (
          <span key={resource.id}>
            {resource.name}
            {resource.version === null
              ? '（无法确认版本）'
              : ` v${resource.version}`}
          </span>
        ))}
      </div>
      <p className={styles.help}>
        创建后，每个任务都会保留本次使用版本；之后切换版本不会影响这些任务。
      </p>
      {state.hasUnavailable && (
        <p className={styles.error} role="alert">
          有资源已停止新任务使用或无法读取，请先在资源库处理后再创建运行。
        </p>
      )}
    </section>
  )
}
