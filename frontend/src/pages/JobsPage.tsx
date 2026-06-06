import { FormEvent, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { createWorkspace, fetchJobs, fetchWorkspaces } from '../api'
import type { JobRecord, WorkspaceRecord } from '../types'

function getErrorStatus(error: unknown): number | undefined {
  if (error && typeof error === 'object' && 'status' in error) {
    return Number((error as { status?: unknown }).status)
  }
  return undefined
}

export function JobsPage() {
  const navigate = useNavigate()
  const params = useParams<{ workspaceId: string }>()
  const selectedWorkspaceId = params.workspaceId ?? 'default'
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([])
  const [workspaceName, setWorkspaceName] = useState('')
  const [disabled, setDisabled] = useState(false)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    let cancelled = false

    Promise.all([fetchWorkspaces(), fetchJobs(selectedWorkspaceId)])
      .then(([workspaceResult, jobResult]) => {
        if (!cancelled) {
          setWorkspaces(workspaceResult.workspaces)
          setJobs(jobResult.jobs)
          setDisabled(false)
          setError('')
        }
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return
        }
        if (getErrorStatus(err) === 404) {
          setDisabled(true)
        } else {
          setError('加载题目任务失败')
        }
      })

    return () => {
      cancelled = true
    }
  }, [selectedWorkspaceId])

  const selectedWorkspace = workspaces.find(
    (workspace) => workspace.id === selectedWorkspaceId
  )

  function handleWorkspaceChange(nextWorkspaceId: string) {
    navigate(nextWorkspaceId === 'default' ? '/workspaces' : `/workspaces/${nextWorkspaceId}`)
  }

  async function handleCreateWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = workspaceName.trim()
    if (!name) {
      return
    }
    setCreating(true)
    setError('')
    try {
      const workspace = await createWorkspace(name)
      setWorkspaces((current) => [...current, workspace])
      setWorkspaceName('')
      navigate(`/workspaces/${workspace.id}`)
    } catch {
      setError('创建工作空间失败')
    } finally {
      setCreating(false)
    }
  }

  if (disabled) {
    return <main className="view">题目工厂未启用</main>
  }

  return (
    <main className="view">
      <header className="topbar">
        <div>
          <p className="phase-name">Agent Legion</p>
          <h1>{selectedWorkspace?.name ?? '题目工厂'}</h1>
        </div>
      </header>

      {error ? <p role="alert">{error}</p> : null}

      <section className="card-outlined form-panel">
        <label className="field">
          <span>工作空间</span>
          <select
            aria-label="工作空间"
            value={selectedWorkspaceId}
            onChange={(event) => handleWorkspaceChange(event.target.value)}
          >
            {workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name}
              </option>
            ))}
          </select>
        </label>
        <form className="inline-form" onSubmit={handleCreateWorkspace}>
          <label className="field">
            <span>新建工作空间</span>
            <input
              aria-label="新建工作空间名称"
              value={workspaceName}
              onChange={(event) => setWorkspaceName(event.target.value)}
              placeholder="例如：初三函数专题"
            />
          </label>
          <button type="submit" disabled={creating || !workspaceName.trim()}>
            {creating ? '创建中…' : '创建'}
          </button>
        </form>
      </section>

      <section className="video-list">
        {jobs.length === 0 ? (
          <p>暂无题目任务</p>
        ) : (
          jobs.map((job) => (
            <article className="card-outlined resource-row" key={job.id}>
              <div className="resource-main">
                <strong>{job.title}</strong>
                <small>{job.source_id}</small>
              </div>
              <span className="status-badge">{job.status}</span>
            </article>
          ))
        )}
      </section>
    </main>
  )
}
