import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  createJobBatch,
  createWorkspace,
  fetchJobs,
  fetchPipelineDefinition,
  fetchWorkspaces,
} from '../api'
import { JOB_STATUS_ICONS, JOB_STATUS_LABELS } from '../labels'
import type {
  JobRecord,
  PipelineDefinitionRecord,
  WorkspaceRecord,
} from '../types'

function getErrorStatus(error: unknown): number | undefined {
  if (error && typeof error === 'object' && 'status' in error) {
    return Number((error as { status?: unknown }).status)
  }
  return undefined
}

function parseQuestionIds(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\n,，\s]+/)
        .map((item) => item.trim())
        .filter(Boolean)
    )
  )
}

export function WorkspacesPage() {
  const navigate = useNavigate()
  const params = useParams<{ workspaceId: string }>()
  const selectedWorkspaceId = params.workspaceId ?? 'default'

  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([])
  const [pipeline, setPipeline] = useState<PipelineDefinitionRecord | null>(
    null
  )
  const [disabled, setDisabled] = useState(false)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [workspaceName, setWorkspaceName] = useState('')
  const [questionInput, setQuestionInput] = useState('')
  const [creatingBatch, setCreatingBatch] = useState(false)
  const [successMessage, setSuccessMessage] = useState('')

  const selectRef = useRef<HTMLElement>(null)
  const dialogRef = useRef<HTMLElement>(null)

  useEffect(() => {
    let cancelled = false

    Promise.all([
      fetchWorkspaces(),
      fetchJobs(selectedWorkspaceId, 'question_content'),
      fetchPipelineDefinition('question_content'),
    ])
      .then(([workspaceResult, jobResult, pipelineResult]) => {
        if (!cancelled) {
          setWorkspaces(workspaceResult.workspaces)
          setJobs(jobResult.jobs)
          setPipeline(pipelineResult.pipeline)
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

  // Sync select value when workspace changes from navigation
  useEffect(() => {
    const select = selectRef.current
    if (select) {
      ;(select as HTMLSelectElement).value = selectedWorkspaceId
    }
  }, [selectedWorkspaceId])

  // md-outlined-select change event
  useEffect(() => {
    const select = selectRef.current
    if (!select) return

    const handleChange = (event: Event) => {
      const target = event.target as HTMLSelectElement
      const nextWorkspaceId = target.value
      navigate(
        nextWorkspaceId === 'default'
          ? '/workspaces'
          : `/workspaces/${nextWorkspaceId}`
      )
    }

    select.addEventListener('change', handleChange)
    return () => {
      select.removeEventListener('change', handleChange)
    }
  }, [navigate])

  // md-dialog close events
  const handleDialogClose = useCallback(() => {
    setDialogOpen(false)
    setWorkspaceName('')
  }, [])

  const handleCreateWorkspace = useCallback(async () => {
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
      setDialogOpen(false)
      navigate(`/workspaces/${workspace.id}`)
    } catch {
      setError('创建工作空间失败')
    } finally {
      setCreating(false)
    }
  }, [workspaceName, navigate])

  const handleCreateBatch = useCallback(async () => {
    const questionIds = parseQuestionIds(questionInput)
    if (questionIds.length === 0) {
      setError('请先输入题目 ID')
      return
    }
    setCreatingBatch(true)
    setError('')
    setSuccessMessage('')
    try {
      const result = await createJobBatch({
        workspaceId: selectedWorkspaceId,
        pipelineKey: 'question_content',
        sourceKind: 'direct_ids',
        inputField: 'question_ids',
        values: questionIds,
      })
      const refreshed = await fetchJobs(selectedWorkspaceId, 'question_content')
      setJobs(refreshed.jobs)
      setQuestionInput('')
      setSuccessMessage(`已创建 ${result.created_count} 个生产任务`)
    } catch {
      setError('创建生产任务失败')
    } finally {
      setCreatingBatch(false)
    }
  }, [questionInput, selectedWorkspaceId])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    dialog.addEventListener('close', handleDialogClose)
    dialog.addEventListener('closed', handleDialogClose)
    return () => {
      dialog.removeEventListener('close', handleDialogClose)
      dialog.removeEventListener('closed', handleDialogClose)
    }
  }, [handleDialogClose])

  const selectedWorkspace = workspaces.find(
    (workspace) => workspace.id === selectedWorkspaceId
  )

  if (disabled) {
    return (
      <main className="view">
        <div
          className="empty-state"
          style={{ textAlign: 'center', marginTop: '120px' }}
        >
          <md-icon
            style={{
              fontSize: '48px',
              color: 'var(--md-sys-color-outline)',
            }}
          >
            factory
          </md-icon>
          <p style={{ marginTop: '16px', fontSize: '16px' }}>题目工厂未启用</p>
        </div>
      </main>
    )
  }

  return (
    <main className="view">
      <header className="topbar">
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '12px',
            minWidth: 0,
          }}
        >
          <Link to="/" style={{ display: 'flex', color: 'inherit' }}>
            <md-icon-button>
              <md-icon>arrow_back</md-icon>
            </md-icon-button>
          </Link>
          <md-icon
            style={{
              fontSize: '28px',
              color: 'var(--md-sys-color-primary)',
              flexShrink: 0,
            }}
          >
            workspaces
          </md-icon>
          <div style={{ minWidth: 0 }}>
            <p className="phase-name">Agent Legion</p>
            <h1>{selectedWorkspace?.name ?? '题目工厂'}</h1>
          </div>
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '12px',
            flexWrap: 'wrap',
          }}
        >
          <md-outlined-select
            ref={selectRef}
            label="工作空间"
            style={{ minWidth: '200px' }}
          >
            {workspaces.map((workspace) => (
              <md-select-option key={workspace.id} value={workspace.id}>
                <div slot="headline">{workspace.name}</div>
              </md-select-option>
            ))}
          </md-outlined-select>
          <md-outlined-button onClick={() => setDialogOpen(true)}>
            新建工作空间
          </md-outlined-button>
        </div>
      </header>

      {error ? (
        <div
          className="card-outlined"
          style={{
            marginTop: '16px',
            padding: '16px',
            display: 'flex',
            alignItems: 'center',
            gap: '12px',
            background: 'var(--md-sys-color-error-container)',
            borderColor: 'var(--md-sys-color-error)',
          }}
        >
          <md-icon style={{ color: 'var(--md-sys-color-on-error-container)' }}>
            error
          </md-icon>
          <span style={{ color: 'var(--md-sys-color-on-error-container)' }}>
            {error}
          </span>
        </div>
      ) : null}

      {successMessage ? (
        <div
          className="card-outlined"
          style={{ marginTop: '16px', padding: '16px' }}
        >
          {successMessage}
        </div>
      ) : null}

      <section
        className="card-outlined"
        style={{ marginTop: '16px', padding: '16px' }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            gap: '16px',
            flexWrap: 'wrap',
          }}
        >
          <div>
            <p className="phase-name">DAG 模板</p>
            <h2 style={{ fontSize: '18px' }}>
              {pipeline?.label ?? '题目内容生成'}
            </h2>
            <p
              style={{
                color: 'var(--md-sys-color-on-surface-variant)',
                marginTop: '4px',
              }}
            >
              {pipeline
                ? `${pipeline.nodes.length} 个节点 · local ${pipeline.concurrency.local} · agent ${pipeline.concurrency.agent}`
                : '加载中'}
            </p>
          </div>
          <md-assist-chip label="question_content" />
        </div>

        {pipeline ? (
          <div
            style={{
              display: 'flex',
              gap: '8px',
              flexWrap: 'wrap',
              marginTop: '12px',
            }}
          >
            {pipeline.nodes.map((node) => (
              <md-assist-chip
                key={node.key}
                label={`${node.key} · ${node.runner}`}
              />
            ))}
          </div>
        ) : null}

        <div style={{ marginTop: '16px', display: 'grid', gap: '12px' }}>
          <md-outlined-text-field
            label="题目 ID"
            aria-label="题目 ID"
            type="textarea"
            rows={4}
            placeholder="每行一个题目 ID，也支持逗号或空格分隔"
            value={questionInput}
            onInput={(event: React.FormEvent<HTMLElement>) =>
              setQuestionInput((event.target as HTMLTextAreaElement).value)
            }
          />
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: '12px',
              flexWrap: 'wrap',
            }}
          >
            <span style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
              将在当前工作空间创建 {parseQuestionIds(questionInput).length}{' '}
              个生产任务
            </span>
            <md-filled-button
              onClick={handleCreateBatch}
              disabled={
                creatingBatch ||
                parseQuestionIds(questionInput).length === 0 ||
                undefined
              }
            >
              {creatingBatch ? '创建中…' : '创建生产任务'}
            </md-filled-button>
          </div>
        </div>
      </section>

      {dialogOpen && (
        <md-dialog
          ref={dialogRef}
          open
          style={
            {
              '--md-dialog-container-color': '#ffffff',
            } as React.CSSProperties
          }
        >
          <div slot="headline">新建工作空间</div>
          <div slot="content">
            <md-outlined-text-field
              label="名称"
              aria-label="名称"
              placeholder="例如：初三函数专题"
              value={workspaceName}
              onInput={(event: React.FormEvent<HTMLElement>) =>
                setWorkspaceName((event.target as HTMLInputElement).value)
              }
              style={{ width: '100%', minWidth: '320px' }}
            />
          </div>
          <div slot="actions">
            <md-text-button onClick={handleDialogClose}>取消</md-text-button>
            <md-filled-button
              onClick={handleCreateWorkspace}
              disabled={creating || !workspaceName.trim() || undefined}
            >
              {creating ? '创建中…' : '创建'}
            </md-filled-button>
          </div>
        </md-dialog>
      )}

      <section className="video-list">
        {jobs.length === 0 ? (
          <div
            className="empty-state"
            style={{ textAlign: 'center', padding: '48px 16px' }}
          >
            <md-icon
              style={{
                fontSize: '48px',
                color: 'var(--md-sys-color-outline)',
              }}
            >
              inbox
            </md-icon>
            <p style={{ marginTop: '16px' }}>暂无题目任务</p>
          </div>
        ) : (
          <md-list>
            {jobs.map((job) => (
              <md-list-item key={job.id} type="button">
                <div slot="headline">{job.title}</div>
                <div slot="supporting-text">{job.source_id}</div>
                <div
                  slot="end"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                  }}
                >
                  <span className={`status-badge ${job.status}`}>
                    <md-icon
                      style={{
                        fontSize: '14px',
                        verticalAlign: 'middle',
                        marginRight: '2px',
                      }}
                    >
                      {JOB_STATUS_ICONS[job.status] || 'help'}
                    </md-icon>
                    {JOB_STATUS_LABELS[job.status] || job.status}
                  </span>
                </div>
              </md-list-item>
            ))}
          </md-list>
        )}
      </section>
    </main>
  )
}
