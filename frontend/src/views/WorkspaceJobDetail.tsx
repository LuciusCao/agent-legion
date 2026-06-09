import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchJobArtifact, fetchJobDetail } from '../api'
import type { JobDetailResponse } from '../types'
import { WORKSPACE_LABELS } from '../labels'

function formatArtifact(content: string): string {
  try {
    return JSON.stringify(JSON.parse(content), null, 2)
  } catch {
    return content
  }
}

export default function WorkspaceJobDetail() {
  const { workspaceId, jobId } = useParams<{
    workspaceId: string
    jobId: string
  }>()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<JobDetailResponse | null>(null)
  const [artifactName, setArtifactName] = useState('')
  const [artifactContent, setArtifactContent] = useState('')
  const [error, setError] = useState('')
  const mounted = useRef(true)
  const artifactRequestId = useRef(0)

  useEffect(() => {
    return () => {
      mounted.current = false
    }
  }, [])

  const formattedArtifact = useMemo(
    () => (artifactContent ? formatArtifact(artifactContent) : ''),
    [artifactContent]
  )

  async function loadDetail() {
    if (!jobId) return
    setError('')
    try {
      const data = await fetchJobDetail(jobId)
      if (!mounted.current) return
      setDetail(data)
    } catch (err) {
      if (!mounted.current) return
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function openArtifact(name: string) {
    if (!jobId) return
    const requestId = ++artifactRequestId.current
    setArtifactName(name)
    setArtifactContent('')
    setError('')
    try {
      const artifact = await fetchJobArtifact(jobId, name)
      if (requestId !== artifactRequestId.current) return
      setArtifactContent(artifact.content)
    } catch (err) {
      if (requestId !== artifactRequestId.current) return
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDetail(null)
    setArtifactName('')
    setArtifactContent('')
    setError('')
    if (!jobId) return
    let stale = false
    fetchJobDetail(jobId)
      .then((data) => {
        if (stale) return
        setDetail(data)
        setError('')
      })
      .catch((err) => {
        if (stale) return
        setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      stale = true
    }
  }, [jobId])

  if (!jobId) {
    return <p className="error-text">缺少任务 ID</p>
  }

  if (!detail && !error) {
    return (
      <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
        加载中...
      </p>
    )
  }

  return (
    <div className="workspace-job-detail">
      <div className="workspace-detail-header">
        <md-text-button
          onClick={() => navigate(`/workspaces/${workspaceId}/jobs`)}
        >
          {WORKSPACE_LABELS.backToJobList}
        </md-text-button>
        <md-outlined-button onClick={loadDetail}>
          {WORKSPACE_LABELS.refresh}
        </md-outlined-button>
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      {detail ? (
        <>
          <section className="card-outlined">
            <h3>{detail.job.title}</h3>
            <p>
              {detail.job.source_id} · {detail.job.status} ·{' '}
              {detail.job.pipeline_key}
            </p>
          </section>

          <section className="card-outlined">
            <h3>{WORKSPACE_LABELS.nodes}</h3>
            <md-list>
              {detail.nodes.map((node) => (
                <md-list-item key={node.node_key}>
                  <div slot="headline">{node.node_key}</div>
                  <div slot="supporting-text">
                    {node.error_message || node.stale_reason || ''}
                  </div>
                  <span slot="end" className={`status-badge ${node.status}`}>
                    {node.status}
                  </span>
                </md-list-item>
              ))}
            </md-list>
          </section>

          <section className="card-outlined">
            <h3>{WORKSPACE_LABELS.runs}</h3>
            <md-list>
              {detail.runs.map((run) => (
                <md-list-item key={run.id}>
                  <div slot="headline">{run.node_key}</div>
                  <div slot="supporting-text">
                    {run.started_at} - {run.finished_at || 'running'}
                    {run.exit_code !== null ? ` · exit: ${run.exit_code}` : ''}
                  </div>
                  <span slot="end" className={`status-badge ${run.status}`}>
                    {run.status}
                  </span>
                </md-list-item>
              ))}
            </md-list>
          </section>

          <section className="card-outlined">
            <h3>Artifacts</h3>
            <md-list>
              {detail.artifacts.map((name) => (
                <md-list-item
                  key={name}
                  type="button"
                  onClick={() => openArtifact(name)}
                >
                  <div slot="headline">{name}</div>
                </md-list-item>
              ))}
            </md-list>
            {artifactName ? <h4>{artifactName}</h4> : null}
            {formattedArtifact ? (
              <pre className="artifact-preview">{formattedArtifact}</pre>
            ) : null}
          </section>
        </>
      ) : null}
    </div>
  )
}
