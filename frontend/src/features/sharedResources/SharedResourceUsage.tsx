import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchRunDetail } from '../../api/sharedResources'
import { sharedResourceKeys } from './sharedResourceQueryKeys'
import type { RunSharedResource } from './sharedResourceTypes'
import styles from './SharedResources.module.css'

export function SharedResourceUsageList({
  resources,
  showHeading = true,
}: {
  resources: RunSharedResource[]
  showHeading?: boolean
}) {
  if (resources.length === 0) return null
  return (
    <section className={styles.usage} aria-label="本次使用版本">
      {showHeading && <h2 className={styles.usageTitle}>本次使用版本</h2>}
      <div className={styles.usageItems}>
        {resources.map((resource) => (
          <span key={resource.resource_id}>
            {resource.name} v{resource.version}
          </span>
        ))}
      </div>
      <p className={styles.help}>
        这里保存创建时的版本，之后切换资源版本不会改变它。
      </p>
    </section>
  )
}

export function RunSharedResourceUsage({
  workspaceId,
  runId,
}: {
  workspaceId: string
  runId: string
}) {
  const [open, setOpen] = useState(false)
  const detailQuery = useQuery({
    queryKey: sharedResourceKeys.run(workspaceId, runId),
    queryFn: ({ signal }) => fetchRunDetail(workspaceId, runId, signal),
    enabled: open,
  })
  return (
    <details
      className={styles.details}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>本次使用版本</summary>
      {open && detailQuery.isLoading && (
        <p className={styles.help}>正在读取…</p>
      )}
      {detailQuery.isError && (
        <p className={styles.error} role="alert">
          本次使用版本读取失败，请稍后重试。
        </p>
      )}
      {detailQuery.data && (
        <SharedResourceUsageList
          resources={detailQuery.data.run.shared_resources ?? []}
          showHeading={false}
        />
      )}
      {detailQuery.data &&
        (detailQuery.data.run.shared_resources ?? []).length === 0 && (
          <p className={styles.help}>本次运行没有使用共享资源。</p>
        )}
    </details>
  )
}
