import { useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { getSharedResourceVersion } from '../../api/sharedResources'
import { formatBytes } from '../../lib/formatters'
import { sharedResourceKeys } from './sharedResourceQueryKeys'
import type { SharedResourceVersion } from './sharedResourceTypes'
import styles from './SharedResources.module.css'

export function ResourceVersionDiff({
  workspaceId,
  resourceId,
  currentVersion,
  versions,
}: {
  workspaceId: string
  resourceId: string
  currentVersion: number
  versions: SharedResourceVersion[]
}) {
  const [selected, setSelected] = useState<number | null>(null)
  const fallback =
    versions.find((version) => version.version !== currentVersion)?.version ??
    currentVersion
  const targetVersion = versions.some((version) => version.version === selected)
    ? selected!
    : fallback
  const details = useQueries({
    queries: [currentVersion, targetVersion].map((version) => ({
      queryKey: sharedResourceKeys.version(workspaceId, resourceId, version),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        getSharedResourceVersion(workspaceId, resourceId, version, signal),
    })),
  })
  const current = details[0].data?.version
  const target = details[1].data?.version
  const paths = Array.from(
    new Set([
      ...(current?.files ?? []).map((file) => file.path),
      ...(target?.files ?? []).map((file) => file.path),
    ])
  ).sort()
  const fileFor = (version: typeof current, path: string) =>
    version?.files?.find((file) => file.path === path)
  const changeLabel = (path: string) => {
    const before = fileFor(current, path)
    const after = fileFor(target, path)
    if (!before) return `v${targetVersion} 新增`
    if (!after) return `v${targetVersion} 移除`
    return before.sha256 === after.sha256 ? '未变化' : '内容变化'
  }

  return (
    <section className={styles.panel} aria-labelledby="resource-diff-title">
      <h2 id="resource-diff-title" className={styles.resourceName}>
        文件差异
      </h2>
      <p className={styles.subtitle}>比较当前使用版本和另一个资源版本。</p>
      <label className={styles.field}>
        比较版本
        <select
          className={styles.select}
          value={targetVersion}
          onChange={(event) => setSelected(Number(event.target.value))}
        >
          {versions.map((version) => (
            <option key={version.id} value={version.version}>
              v{version.version}
              {version.version === currentVersion ? '（当前使用）' : ''}
            </option>
          ))}
        </select>
      </label>
      {details.some((query) => query.isLoading) ? (
        <p className={styles.help}>正在读取文件差异…</p>
      ) : details.some((query) => query.isError) ? (
        <p className={styles.error} role="alert">
          文件差异加载失败，请重新打开管理页再试。
        </p>
      ) : paths.length === 0 ? (
        <p className={styles.help}>这两个版本都没有文件。</p>
      ) : (
        <table className={styles.diffTable}>
          <thead>
            <tr>
              <th>文件</th>
              <th>v{currentVersion}</th>
              <th>v{targetVersion}</th>
              <th>差异</th>
            </tr>
          </thead>
          <tbody>
            {paths.map((path) => {
              const before = fileFor(current, path)
              const after = fileFor(target, path)
              return (
                <tr key={path}>
                  <td className={styles.diffPath} data-label="文件">
                    {path}
                  </td>
                  <td data-label={`v${currentVersion}`}>
                    {before ? formatBytes(before.size_bytes) : '不存在'}
                  </td>
                  <td data-label={`v${targetVersion}`}>
                    {after ? formatBytes(after.size_bytes) : '不存在'}
                  </td>
                  <td data-label="差异">{changeLabel(path)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {[current, target].map(
        (version) =>
          version && (
            <details className={styles.details} key={version.id}>
              <summary>查看 v{version.version} 文件与高级详情</summary>
              <p className={styles.help}>
                共 {version.file_count} 个文件，合计{' '}
                {formatBytes(version.total_size_bytes)}。
              </p>
              <span className={styles.hash}>{version.manifest_hash}</span>
            </details>
          )
      )}
    </section>
  )
}
