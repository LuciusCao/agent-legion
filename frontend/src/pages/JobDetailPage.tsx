import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import type { DagEdge, DagGraphNode } from '../components/DagGraph'
import { JobProgressPanel } from '../components/JobProgressPanel'
import { QuestionContentPanel } from '../components/QuestionContentPanel'
import { fetchJobArtifact, fetchJobDetail, deleteJob } from '../api'
import { rerunJob, runToJob, packageJobs } from '../jobApi'
import { useUiStore } from '../stores/uiStore'
import { useJobStore } from '../stores/jobStore'
import type {
  JobDetailResponse,
  JobNodeRecord,
  WorkflowDefinitionRecord,
} from '../types'
import styles from './JobDetailPage.module.css'
import { ArtifactListDialog } from '../components/ArtifactListDialog'
import { ArtifactPreviewDialog } from '../components/ArtifactPreviewDialog'
import { DagFullscreenDialog } from '../components/DagFullscreenDialog'
import { JobDetailActions } from '../components/JobDetailActions'

const VALID_STATUSES = new Set<DagGraphNode['status']>([
  'pending',
  'running',
  'completed',
  'failed',
  'stale',
])

const POLLING_STATUSES = new Set(['queued', 'running'])

function normalizeStatus(status: string): DagGraphNode['status'] {
  if (VALID_STATUSES.has(status as DagGraphNode['status'])) {
    return status as DagGraphNode['status']
  }
  return 'pending'
}

function computeNodeDuration(
  startedAt?: string | null,
  finishedAt?: string | null
): number | undefined {
  const start = startedAt ? new Date(startedAt).getTime() : NaN
  if (Number.isNaN(start)) return undefined
  if (finishedAt) {
    const end = new Date(finishedAt).getTime()
    if (Number.isNaN(end)) return undefined
    return (end - start) / 1000
  }
  return (Date.now() - start) / 1000
}

function toDagNodes(nodes: JobNodeRecord[]): DagGraphNode[] {
  return nodes.map((n) => ({
    key: n.node_key,
    label: n.label || n.node_key,
    status: normalizeStatus(n.status),
    inputs: n.inputs,
    outputs: n.outputs,
    duration: computeNodeDuration(n.started_at, n.finished_at),
    executorKind: (n.executor_kind as DagGraphNode['executorKind']) ?? null,
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

function toWorkflowDefinition(
  detail: JobDetailResponse | null
): WorkflowDefinitionRecord | null {
  if (!detail) return null
  return {
    key: detail.job.workflow_key,
    label: detail.job.workflow_key,
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
  const workflowDefinition = useMemo(
    () => toWorkflowDefinition(detail),
    [detail]
  )
  const questionArtifactRefreshKey = useMemo(() => {
    const producer = detail?.nodes.find((node) =>
      node.outputs?.includes('questions.json')
    )
    if (!producer) return ''
    return [producer.status, producer.started_at, producer.finished_at].join(
      ':'
    )
  }, [detail])

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

  useEffect(() => {
    if (!detail) {
      setDetailPageActions(null)
      return
    }
    setDetailPageActions(
      <JobDetailActions
        jobs={[detail.job]}
        workflowDefinition={workflowDefinition}
        loading={actionLoading}
        onRerun={handleRerun}
        onRunTo={handleRunTo}
        onContinue={handleContinue}
        onPackage={handlePackage}
        onDelete={handleDelete}
        onOpenArtifacts={() => setArtifactListOpen(true)}
      />
    )
    return () => {
      setDetailPageActions(null)
    }
  }, [
    detail,
    setDetailPageActions,
    workflowDefinition,
    actionLoading,
    handleRerun,
    handleRunTo,
    handleContinue,
    handlePackage,
    handleDelete,
  ])

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

      <div className={styles.columns}>
        <div className={styles.left}>
          {detail?.job.source_type === 'question' && jobId && (
            <QuestionContentPanel
              key={jobId}
              jobId={jobId}
              refreshKey={questionArtifactRefreshKey}
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
        jobId={jobId}
        nodes={dagNodes}
        edges={dagEdges}
        runs={detail?.runs}
        onClose={() => setDagDialogOpen(false)}
      />
    </div>
  )
}
