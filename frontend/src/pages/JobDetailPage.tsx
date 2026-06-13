import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import type { DagEdge, DagNode } from '../components/DagGraph'
import { JobProgressPanel } from '../components/JobProgressPanel'
import { QuestionContentPanel } from '../components/QuestionContentPanel'
import { fetchJobArtifact, fetchJobDetail, deleteJob } from '../api'
import { rerunJob, runToJob, packageJobs } from '../jobApi'
import { durationSeconds } from '../helpers'
import { useUiStore } from '../stores/uiStore'
import { useJobStore } from '../stores/jobStore'
import type {
  JobDetailResponse,
  JobNodeRecord,
  PipelineDefinitionRecord,
} from '../types'
import styles from './JobDetailPage.module.css'
import { ArtifactListDialog } from '../components/ArtifactListDialog'
import { ArtifactPreviewDialog } from '../components/ArtifactPreviewDialog'
import { DagFullscreenDialog } from '../components/DagFullscreenDialog'
import { JobActionBar } from '../components/JobActionBar'

const VALID_STATUSES = new Set<DagNode['status']>([
  'pending',
  'running',
  'completed',
  'failed',
])

const POLLING_STATUSES = new Set(['queued', 'running'])

function normalizeStatus(status: string): DagNode['status'] {
  if (VALID_STATUSES.has(status as DagNode['status'])) {
    return status as DagNode['status']
  }
  return 'pending'
}

function toDagNodes(nodes: JobNodeRecord[]): DagNode[] {
  return nodes.map((n) => ({
    key: n.node_key,
    label: n.label || n.node_key,
    status: normalizeStatus(n.status),
    inputs: n.inputs,
    outputs: n.outputs,
    duration: durationSeconds(n.started_at, n.finished_at),
  }))
}

function toDagEdges(nodes: JobNodeRecord[]): DagEdge[] {
  const edges: DagEdge[] = []
  nodes.forEach((node) => {
    if (node.after && Array.isArray(node.after)) {
      node.after.forEach((fromKey) => {
        edges.push({ from: fromKey, to: node.node_key })
      })
    }
  })
  return edges
}

function toPipelineDefinition(
  detail: JobDetailResponse | null
): PipelineDefinitionRecord | null {
  if (!detail) return null
  return {
    key: detail.job.pipeline_key,
    label: detail.job.pipeline_key,
    intake: { modes: [] },
    nodes: detail.nodes.map((n) => ({
      key: n.node_key,
      label: n.label,
      after: n.after,
      capability: n.capability,
      inputs: n.inputs,
      outputs: n.outputs,
    })),
  }
}

export default function JobDetailPage() {
  const { workspaceId, jobId } = useParams<{
    workspaceId: string
    jobId: string
  }>()
  const navigate = useNavigate()
  const { setPageTitle, setDetailPageActions } = useUiStore()
  const [detail, setDetail] = useState<JobDetailResponse | null>(null)
  const [error, setError] = useState('')
  const [artifactListOpen, setArtifactListOpen] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewArtifact, setPreviewArtifact] = useState<{
    name: string
    content: string
  } | null>(null)
  const [dagDialogOpen, setDagDialogOpen] = useState(false)
  const artifactRequestId = useRef(0)

  const refreshDetail =
    useCallback(async (): Promise<JobDetailResponse | null> => {
      if (!jobId) return null
      try {
        const data = await fetchJobDetail(jobId)
        setDetail(data)
        setError('')
        return data
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
        return null
      }
    }, [jobId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDetail(null)
    setError('')
    setArtifactListOpen(false)
    setPreviewOpen(false)
    setPreviewArtifact(null)
    if (!jobId) return
    let stale = false
    refreshDetail().then((data) => {
      if (stale || !data) return
      setPageTitle(data.job.title || data.job.source_id || '任务详情')
    })
    return () => {
      stale = true
      setPageTitle(null)
      setArtifactListOpen(false)
      setPreviewOpen(false)
      setPreviewArtifact(null)
    }
  }, [jobId, setPageTitle, refreshDetail])

  useEffect(() => {
    if (!detail) {
      setDetailPageActions(null)
      return
    }
    setDetailPageActions(
      <md-icon-button
        aria-label="产物文件"
        onClick={() => setArtifactListOpen(true)}
      >
        <md-icon>folder_open</md-icon>
      </md-icon-button>
    )
    return () => {
      setDetailPageActions(null)
    }
  }, [detail, setDetailPageActions])

  // Poll every 5s for queued and running jobs only
  const detailRef = useRef(detail)
  useEffect(() => {
    detailRef.current = detail
  }, [detail])
  useEffect(() => {
    if (!jobId) return
    let stale = false
    const timer = setInterval(() => {
      const status = detailRef.current?.job.status
      if (status && POLLING_STATUSES.has(status)) {
        refreshDetail().then((data) => {
          if (stale || !data) return
          setDetail(data)
        })
      }
    }, 5000)
    return () => {
      stale = true
      clearInterval(timer)
    }
  }, [jobId, refreshDetail])

  const dagNodes = useMemo(
    () => (detail ? toDagNodes(detail.nodes) : []),
    [detail]
  )
  const dagEdges = useMemo(
    () => (detail ? toDagEdges(detail.nodes) : []),
    [detail]
  )
  const pipelineDefinition = useMemo(
    () => toPipelineDefinition(detail),
    [detail]
  )

  const [actionLoading, setActionLoading] = useState(false)

  const handleRerun = useCallback(
    async (nodeKey: string) => {
      if (!jobId) return
      setActionLoading(true)
      try {
        await rerunJob(jobId, nodeKey)
        await refreshDetail()
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setActionLoading(false)
      }
    },
    [jobId, refreshDetail]
  )

  const handleRunTo = useCallback(
    async (targetKey: string, startKey?: string) => {
      if (!jobId) return
      setActionLoading(true)
      try {
        await runToJob(jobId, targetKey, startKey)
        await refreshDetail()
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setActionLoading(false)
      }
    },
    [jobId, refreshDetail]
  )

  const handleContinue = useCallback(async () => {
    if (!jobId) return
    setActionLoading(true)
    try {
      await useJobStore.getState().continueJob(jobId)
      await refreshDetail()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setActionLoading(false)
    }
  }, [jobId, refreshDetail])

  const handlePackage = useCallback(async () => {
    if (!workspaceId || !jobId) return
    setActionLoading(true)
    try {
      const result = await packageJobs(workspaceId, [jobId])
      if (result.download_url) {
        window.open(result.download_url, '_blank')
      }
      await refreshDetail()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setActionLoading(false)
    }
  }, [workspaceId, jobId, refreshDetail])

  const handleDelete = useCallback(async () => {
    if (!jobId || !workspaceId) return
    setActionLoading(true)
    try {
      await deleteJob(jobId)
      navigate(`/workspaces/${encodeURIComponent(workspaceId)}/jobs`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setActionLoading(false)
    }
  }, [jobId, workspaceId, navigate])

  async function openArtifact(name: string) {
    if (!jobId) return
    const requestId = ++artifactRequestId.current
    setArtifactListOpen(false)
    setPreviewArtifact({ name, content: '' })
    setPreviewOpen(true)
    try {
      const artifact = await fetchJobArtifact(jobId, name)
      if (requestId !== artifactRequestId.current) return
      setPreviewArtifact({ name, content: artifact.content })
    } catch (err) {
      if (requestId !== artifactRequestId.current) return
      setPreviewArtifact({
        name,
        content: err instanceof Error ? err.message : String(err),
      })
    }
  }

  if (!jobId) {
    return <p className="error-text">缺少任务 ID</p>
  }

  if (!detail && !error) {
    return <p className={styles.loading}>加载中...</p>
  }

  return (
    <div className={styles.page}>
      {error ? <p className={styles.error}>{error}</p> : null}

      {detail && (
        <div className={styles.actionBarRow}>
          <JobActionBar
            jobs={[detail.job]}
            pipelineDefinition={pipelineDefinition}
            mode="single"
            loading={actionLoading}
            onRerun={handleRerun}
            onRunTo={handleRunTo}
            onContinue={handleContinue}
            onPackage={handlePackage}
            onDelete={handleDelete}
          />
        </div>
      )}

      <div className={styles.columns}>
        <div className={styles.left}>
          {detail?.job.source_type === 'question' && workspaceId && (
            <QuestionContentPanel
              workspaceId={workspaceId}
              questionId={detail.job.source_id}
            />
          )}
        </div>

        <div className={styles.right}>
          {detail && (
            <JobProgressPanel
              jobId={jobId}
              nodes={detail.nodes}
              runs={detail.runs}
              onOpenDagDialog={() => setDagDialogOpen(true)}
            />
          )}
        </div>
      </div>
      {detail && (
        <ArtifactListDialog
          open={artifactListOpen}
          artifacts={detail.artifacts}
          onClose={() => setArtifactListOpen(false)}
          onSelect={openArtifact}
        />
      )}
      {previewArtifact && (
        <ArtifactPreviewDialog
          open={previewOpen}
          name={previewArtifact.name}
          content={previewArtifact.content}
          onClose={() => setPreviewOpen(false)}
        />
      )}
      <DagFullscreenDialog
        open={dagDialogOpen}
        nodes={dagNodes}
        edges={dagEdges}
        onClose={() => setDagDialogOpen(false)}
      />
    </div>
  )
}
