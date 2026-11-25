import { useQueries, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchJobDetail, fetchJobsSnapshot } from '../../api'
import styles from './SharedResources.module.css'

const HISTORY_LIMIT = 12

export function ResourceJobHistory({
  workspaceId,
  resourceId,
  resourceName,
  currentVersion,
}: {
  workspaceId: string
  resourceId: string
  resourceName: string
  currentVersion: number | null
}) {
  const jobsQuery = useQuery({
    queryKey: ['shared-resource-jobs', workspaceId, resourceId],
    queryFn: () => fetchJobsSnapshot(workspaceId, HISTORY_LIMIT),
  })
  const jobs = jobsQuery.data?.jobs ?? []
  const detailQueries = useQueries({
    queries: jobs.map((job) => ({
      queryKey: ['shared-resource-job-detail', job.id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchJobDetail(job.id, signal),
    })),
  })
  const rows = detailQueries.flatMap((query, index) => {
    const detail = query.data
    const pin = detail?.shared_resources?.find(
      (resource) => resource.resource_id === resourceId
    )
    return detail && pin
      ? [{ job: jobs[index], detail, version: pin.version }]
      : []
  })

  return (
    <section className={styles.panel} aria-labelledby="resource-jobs-title">
      <h2 id="resource-jobs-title" className={styles.resourceName}>
        新旧任务版本比较
      </h2>
      <p className={styles.subtitle}>
        读取最近任务的实际使用版本；切换当前版本不会改写任务历史。
      </p>
      {jobsQuery.isLoading || detailQueries.some((query) => query.isLoading) ? (
        <p className={styles.help}>正在读取任务版本…</p>
      ) : jobsQuery.isError ? (
        <p className={styles.error} role="alert">
          任务列表加载失败，请稍后重试。
        </p>
      ) : rows.length === 0 ? (
        <p className={styles.help}>
          最近还没有任务使用过这个资源。创建任务后，可在这里比较新旧版本。
        </p>
      ) : (
        <div className={styles.jobList}>
          {rows.map(({ job, version }) => (
            <div className={styles.jobRow} key={job.id}>
              <div>
                <div className={styles.jobName}>{job.title || job.id}</div>
                <div className={styles.jobMeta}>
                  {resourceName} · 本次使用 v{version}
                </div>
                <div className={styles.jobMeta}>
                  {currentVersion === version
                    ? '与当前使用版本相同'
                    : currentVersion === null
                      ? '当前尚未选择版本，本任务不变'
                      : `当前已改为 v${currentVersion}，本任务不变`}
                </div>
              </div>
              <Link to={`/workspaces/${workspaceId}/jobs/${job.id}`}>
                查看任务
              </Link>
            </div>
          ))}
        </div>
      )}
      {detailQueries.some((query) => query.isError) && (
        <p className={`${styles.notice} ${styles.warningNotice}`}>
          部分任务版本读取失败，其他任务仍可比较。
        </p>
      )}
    </section>
  )
}
