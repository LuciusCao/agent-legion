import { useEffect, useState } from 'react'
import { Button } from '@mui/material'
import { api } from '../../api'
import type { components } from '../../generated/api'
import styles from './WorkflowNodeCodeSection.module.css'

type VersionsResponse =
  components['schemas']['WorkflowNodeCodeVersionsResponse']
type VersionSummary = components['schemas']['WorkflowNodeCodeVersionSummary']

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  archived: '已归档',
}

function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function VersionRow(props: {
  version: VersionSummary
  onRollback: (version: number) => void
  disabled: boolean
}) {
  const { version } = props
  return (
    <div className={styles.versionRow}>
      <span className={styles.versionTitle}>
        v{version.version} · {STATUS_LABELS[version.status] ?? version.status}
      </span>
      <span className={styles.versionMeta}>
        {version.created_by}
        {version.change_note ? ` · ${version.change_note}` : ''} ·{' '}
        {formatTime(version.created_at)}
      </span>
      {version.status !== 'published' && (
        <Button
          variant="text"
          size="small"
          onClick={() => props.onRollback(version.version)}
          disabled={props.disabled}
        >
          回滚到此版本
        </Button>
      )}
    </div>
  )
}

// Version history list for one node; rolls a published/archived version back
// by re-publishing it as a new version (versions stay immutable server-side).
export function WorkflowNodeCodeVersions(props: {
  url: string
  onRollback: (version: number) => void
  disabled: boolean
}) {
  const [versions, setVersions] = useState<VersionSummary[] | null>(null)
  const [error, setError] = useState('')

  // Keyed by a reload token in the parent, so the effect only runs on mount.
  useEffect(() => {
    let cancelled = false
    api<VersionsResponse>(props.url)
      .then((result) => {
        if (!cancelled) setVersions(result.versions)
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : '加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [props.url])

  if (error) {
    return (
      <div role="alert" className={styles.error}>
        {error}
      </div>
    )
  }
  if (versions === null) {
    return <div className={styles.hint}>加载版本历史中...</div>
  }
  if (versions.length === 0) {
    return <div className={styles.hint}>暂无自定义版本。</div>
  }
  return (
    <div className={styles.versions}>
      {versions.map((version) => (
        <VersionRow
          key={version.id}
          version={version}
          onRollback={props.onRollback}
          disabled={props.disabled}
        />
      ))}
    </div>
  )
}
