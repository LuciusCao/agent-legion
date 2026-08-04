import styles from './JobListSkeleton.module.css'

const ROW_COUNT = 10

export function JobListSkeleton() {
  return (
    <div
      className={styles.container}
      data-testid="job-list-skeleton"
      aria-busy="true"
      aria-label="加载任务列表"
    >
      {Array.from({ length: ROW_COUNT }).map((_, index) => (
        <div key={index} className={styles.row} data-testid="skeleton-row">
          <div className={styles.checkbox} />
          <div className={styles.main}>
            <div className={styles.title} />
            <div className={styles.description} />
          </div>
          <div className={styles.statusEnd}>
            <div className={styles.activeLabel} />
            <div className={styles.badge} />
          </div>
        </div>
      ))}
    </div>
  )
}
