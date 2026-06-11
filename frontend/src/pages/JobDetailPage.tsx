import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { DagGraph, type DagEdge, type DagNode } from '../components/DagGraph'
import { NodeDetailPanel } from '../components/NodeDetailPanel'
import { JobProgressPanel } from '../components/JobProgressPanel'
import { QuestionContentPanel } from '../components/QuestionContentPanel'
import { fetchJobArtifact, fetchJobDetail } from '../api'
import { durationSeconds } from '../helpers'
import { useUiStore } from '../stores/uiStore'
import type { JobDetailResponse, JobNodeRecord } from '../types'
import styles from './JobDetailPage.module.css'
import { ArtifactListDialog } from '../components/ArtifactListDialog'
import { ArtifactPreviewDialog } from '../components/ArtifactPreviewDialog'
import { DagFullscreenDialog } from '../components/DagFullscreenDialog'

const VALID_STATUSES = new Set<DagNode['status']>([
  'pending',
  'running',
  'completed',
  'failed',
])

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

export default function JobDetailPage() {
  const { workspaceId, jobId } = useParams<{
    workspaceId: string
    jobId: string
  }>()
  const { setPageTitle, setDetailPageActions } = useUiStore()
  const [detail, setDetail] = useState<JobDetailResponse | null>(null)
  const [error, setError] = useState('')
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)
  const [artifactListOpen, setArtifactListOpen] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewArtifact, setPreviewArtifact] = useState<{
    name: string
    content: string
  } | null>(null)
  const [dagExpanded, setDagExpanded] = useState(true)
  const [dagDialogOpen, setDagDialogOpen] = useState(false)
  const artifactRequestId = useRef(0)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDetail(null)
    setError('')
    setSelectedNodeKey(null)
    setArtifactListOpen(false)
    setPreviewOpen(false)
    setPreviewArtifact(null)
    if (!jobId) return
    let stale = false
    fetchJobDetail(jobId)
      .then((data) => {
        if (stale) return
        setDetail(data)
        setPageTitle(data.job.title || data.job.source_id || '任务详情')
      })
      .catch((err) => {
        if (stale) return
        setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      stale = true
      setPageTitle(null)
      setArtifactListOpen(false)
      setPreviewOpen(false)
      setPreviewArtifact(null)
    }
  }, [jobId, setPageTitle])

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

  // Poll every 5s for running jobs
  const detailRef = useRef(detail)
  useEffect(() => {
    detailRef.current = detail
  }, [detail])
  useEffect(() => {
    if (!jobId) return
    let stale = false
    const timer = setInterval(() => {
      if (detailRef.current?.job.status === 'running') {
        fetchJobDetail(jobId)
          .then((data) => {
            if (!stale) setDetail(data)
          })
          .catch(() => {})
      }
    }, 5000)
    return () => {
      stale = true
      clearInterval(timer)
    }
  }, [jobId])

  const dagNodes = useMemo(
    () => (detail ? toDagNodes(detail.nodes) : []),
    [detail]
  )
  const dagEdges = useMemo(
    () => (detail ? toDagEdges(detail.nodes) : []),
    [detail]
  )

  const selectedNode = useMemo(() => {
    if (!detail || !selectedNodeKey) return null
    const n = detail.nodes.find((item) => item.node_key === selectedNodeKey)
    if (!n) return null
    return {
      key: n.node_key,
      label: n.label || n.node_key,
      status: normalizeStatus(n.status),
      startedAt: n.started_at || undefined,
      endedAt: n.finished_at || undefined,
      duration: durationSeconds(n.started_at, n.finished_at),
      agentId: undefined,
      errorMessage: n.error_message || undefined,
    }
  }, [detail, selectedNodeKey])

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
          {detail?.job.source_type === 'question' && workspaceId && (
            <QuestionContentPanel
              workspaceId={workspaceId}
              questionId={detail.job.source_id}
            />
          )}
          <div
            className={`${styles.graphWrap} ${dagExpanded ? styles.graphExpanded : styles.graphCollapsed}`}
          >
            <div className={styles.graphHeader}>
              <span className={styles.graphTitle}>DAG 流水线</span>
              <md-icon-button
                aria-label={dagExpanded ? '收起' : '展开'}
                onClick={() => setDagExpanded((v) => !v)}
              >
                <md-icon>{dagExpanded ? 'expand_less' : 'expand_more'}</md-icon>
              </md-icon-button>
            </div>
            {dagExpanded && (
              <div className={styles.graphBody}>
                <DagGraph
                  nodes={dagNodes}
                  edges={dagEdges}
                  selectedNodeKey={selectedNodeKey}
                  onNodeClick={setSelectedNodeKey}
                />
              </div>
            )}
          </div>
          <NodeDetailPanel
            node={selectedNode}
            onViewLogs={() => {
              /* TODO view logs */
            }}
            onRerunNode={() => {
              /* TODO rerun node */
            }}
          />
        </div>

        <div className={styles.right}>
          {detail && (
            <JobProgressPanel
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
        selectedNodeKey={selectedNodeKey}
        onNodeClick={setSelectedNodeKey}
        onClose={() => setDagDialogOpen(false)}
      />
    </div>
  )
}
