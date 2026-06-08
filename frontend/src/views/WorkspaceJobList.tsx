import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useVideoStore } from '../stores/videoStore'
import { useVideoEvents } from '../hooks/useVideoEvents'
import { createJobBatch, fetchJobs, fetchPipelineDefinition } from '../api'
import type { JobRecord, PipelineDefinitionRecord } from '../types'

function parseBatchInput(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\s,，]+/)
        .map((item) => item.trim())
        .filter(Boolean)
    )
  )
}

type Props = {
  isVideoHive: boolean
}

export default function WorkspaceJobList({ isVideoHive }: Props) {
  const navigate = useNavigate()
  const { currentWorkspace } = useWorkspaceStore()
  const { videos, fetchVideos } = useVideoStore()

  const [pipeline, setPipeline] = useState<PipelineDefinitionRecord | null>(null)
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [selectedModeKey, setSelectedModeKey] = useState('')
  const [inputValue, setInputValue] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const workspaceId = currentWorkspace?.id
  const pipelineKey = currentWorkspace?.default_pipeline_key || 'question_content'

  useVideoEvents(isVideoHive)

  useEffect(() => {
    if (isVideoHive) {
      fetchVideos()
    }
  }, [isVideoHive, fetchVideos])

  useEffect(() => {
    if (!isVideoHive && workspaceId) {
      let cancelled = false
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setError('')

      fetchPipelineDefinition(pipelineKey)
        .then((result) => {
          if (cancelled) return
          setPipeline(result.pipeline)
          setSelectedModeKey(result.pipeline.intake?.modes[0]?.key || '')
        })
        .catch((err) => {
          if (cancelled) return
          setError(err instanceof Error ? err.message : String(err))
        })

      fetchJobs(workspaceId)
        .then((result) => {
          if (cancelled) return
          setJobs(result.jobs)
        })
        .catch((err) => {
          if (cancelled) return
          setError((prev) => prev || (err instanceof Error ? err.message : String(err)))
        })

      return () => {
        cancelled = true
      }
    }
  }, [isVideoHive, workspaceId, pipelineKey])

  async function handleCreateBatch() {
    const selectedMode = pipeline?.intake?.modes.find((mode) => mode.key === selectedModeKey)
    const values = parseBatchInput(inputValue)
    if (!selectedMode || values.length === 0 || !workspaceId) {
      setMessage('')
      setError('请输入至少一个值')
      return
    }
    setSubmitting(true)
    setMessage('')
    setError('')
    try {
      const result = await createJobBatch({
        workspaceId,
        pipelineKey,
        sourceKind: selectedMode.key,
        inputField: selectedMode.input_field,
        values,
      })
      setError('')
      setMessage(`已创建 ${result.created_count} 个题目任务`)
      setInputValue('')
      try {
        const refreshed = await fetchJobs(workspaceId)
        setJobs(refreshed.jobs)
      } catch (refreshErr) {
        // refresh failure should not erase success message
        console.error('Failed to refresh jobs:', refreshErr)
      }
    } catch (err) {
      setMessage('')
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  if (isVideoHive) {
    return (
      <div>
        <h3>视频队列</h3>
        {videos.length === 0 ? (
          <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>暂无视频</p>
        ) : (
          <md-list>
            {videos.map((video) => (
              <md-list-item
                key={video.id}
                type="button"
                onClick={() => navigate(`/videos/${video.id}`)}
              >
                <div slot="headline">{video.title || video.external_id}</div>
                <div slot="supporting-text">
                  {video.status} · {video.content_type}
                </div>
              </md-list-item>
            ))}
          </md-list>
        )}
      </div>
    )
  }

  return (
    <div>
      <section className="card-outlined workspace-job-create">
        <h3>{pipeline?.label || currentWorkspace?.name || '题目生产'}</h3>
        <div className="intake-chip-row">
          {pipeline?.intake?.modes.map((mode) => (
            <md-filter-chip
              key={mode.key}
              label={mode.label}
              selected={selectedModeKey === mode.key}
              onClick={() => setSelectedModeKey(mode.key)}
            />
          ))}
        </div>
        <md-outlined-text-field
          label={pipeline?.intake?.modes.find((mode) => mode.key === selectedModeKey)?.label || '输入'}
          aria-label={pipeline?.intake?.modes.find((mode) => mode.key === selectedModeKey)?.label || '输入'}
          type="textarea"
          rows={5}
          value={inputValue}
          onInput={(event: Event) => setInputValue((event.target as HTMLInputElement).value)}
        />
        <md-filled-button disabled={submitting} onClick={handleCreateBatch}>
          创建任务
        </md-filled-button>
        {message ? <p className="success-text">{message}</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
      </section>

      <section className="card-outlined workspace-job-list">
        <h3>任务列表</h3>
        <md-list>
          {jobs.map((job) => (
            <md-list-item
              key={job.id}
              type="button"
              onClick={() => navigate(`/workspaces/${workspaceId}/jobs/${job.id}`)}
            >
              <div slot="headline">{job.title}</div>
              <div slot="supporting-text">{job.source_id}</div>
              <span slot="end" className={`status-badge ${job.status}`}>{job.status}</span>
            </md-list-item>
          ))}
        </md-list>
      </section>
    </div>
  )
}
