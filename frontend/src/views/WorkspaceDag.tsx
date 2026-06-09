import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchWorkspaceDag } from '../api'
import type { WorkspaceDagResponse } from '../types'

const STATUSES = ['pending', 'running', 'completed', 'failed', 'stale']

export default function WorkspaceDag() {
  const { workspaceId = 'default' } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const [dag, setDag] = useState<WorkspaceDagResponse | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchWorkspaceDag(workspaceId)
      .then(setDag)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [workspaceId])

  if (error) return <p className="error-text">{error}</p>
  if (!dag) return <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>加载中...</p>

  return (
    <div>
      <section className="card-outlined" style={{ padding: 16, marginBottom: 16 }}>
        <h3>{dag.pipeline.label}</h3>
        <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
          {dag.pipeline.key} · local {dag.pipeline.concurrency.local} · agent {dag.pipeline.concurrency.agent}
        </p>
      </section>

      <div style={{ display: 'grid', gap: 12 }}>
        {dag.nodes.map((node) => (
          <section
            key={node.key}
            className="card-outlined"
            style={{ padding: 16, cursor: 'pointer' }}
            onClick={() => navigate(`/workspaces/${workspaceId}/runs?node_key=${encodeURIComponent(node.key)}`)}
          >
            <h4 style={{ margin: 0 }}>{node.key}</h4>
            <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
              {node.runner} · after {node.after.length ? node.after.join(', ') : 'start'}
            </p>
            <p>{STATUSES.map((status) => `${status} ${node.status_counts[status] || 0}`).join(' · ')}</p>
            <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
              outputs: {node.outputs.length ? node.outputs.join(', ') : 'none'}
            </p>
          </section>
        ))}
      </div>
    </div>
  )
}
