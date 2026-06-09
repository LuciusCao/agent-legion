import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { fetchWorkspaceRuns } from '../api'
import type { WorkspaceRunRecord } from '../types'

export default function WorkspaceRuns() {
  const { workspaceId = 'default' } = useParams<{ workspaceId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const [runs, setRuns] = useState<WorkspaceRunRecord[]>([])
  const [error, setError] = useState('')

  const status = searchParams.get('status') || ''
  const nodeKey = searchParams.get('node_key') || ''

  useEffect(() => {
    let cancelled = false
    fetchWorkspaceRuns(workspaceId, {
      status: status || undefined,
      nodeKey: nodeKey || undefined,
    })
      .then((response) => {
        if (cancelled) return
        setRuns(response.runs)
        setError('')
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [workspaceId, status, nodeKey])

  const counts = useMemo(
    () =>
      runs.reduce<Record<string, number>>((acc, run) => {
        acc[run.status] = (acc[run.status] || 0) + 1
        return acc
      }, {}),
    [runs]
  )

  function updateStatus(nextStatus: string) {
    const next = new URLSearchParams(searchParams)
    if (nextStatus) next.set('status', nextStatus)
    else next.delete('status')
    setSearchParams(next)
  }

  return (
    <div>
      <section
        className="card-outlined"
        style={{ padding: 16, marginBottom: 16 }}
      >
        <h3>Runs</h3>
        <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
          total {runs.length} · running {counts.running || 0} · completed{' '}
          {counts.completed || 0} · failed {counts.failed || 0}
        </p>
        <md-outlined-select
          aria-label="Run status"
          value={status}
          onInput={(event: Event) =>
            updateStatus((event.target as HTMLSelectElement).value)
          }
        >
          <md-select-option value="">
            <div slot="headline">全部</div>
          </md-select-option>
          <md-select-option value="running">
            <div slot="headline">running</div>
          </md-select-option>
          <md-select-option value="completed">
            <div slot="headline">completed</div>
          </md-select-option>
          <md-select-option value="failed">
            <div slot="headline">failed</div>
          </md-select-option>
        </md-outlined-select>
      </section>

      {error ? <p className="error-text">{error}</p> : null}
      {runs.length === 0 ? (
        <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
          暂无运行记录
        </p>
      ) : (
        <md-list>
          {runs.map((run) => (
            <md-list-item
              key={run.id}
              type="button"
              onClick={() =>
                navigate(`/workspaces/${workspaceId}/jobs/${run.job_id}`)
              }
            >
              <div slot="headline">{run.job_title}</div>
              <div slot="supporting-text">
                {run.node_key} · {run.source_id} · {run.started_at}
              </div>
              <span slot="end" className={`status-badge ${run.status}`}>
                {run.status}
              </span>
            </md-list-item>
          ))}
        </md-list>
      )}
    </div>
  )
}
