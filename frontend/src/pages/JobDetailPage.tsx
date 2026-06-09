import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { DagGraph, type DagEdge, type DagNode } from '../components/DagGraph'
import { NodeDetailPanel } from '../components/NodeDetailPanel'
import { NodeRunsTable, type NodeRun } from '../components/NodeRunsTable'
import { fetchJobArtifact, fetchJobDetail } from '../api'
import { JOB_STATUS_LABELS } from '../labels'
import type { JobDetailResponse, JobNodeRecord, NodeRunRecord } from '../types'
import styles from './JobDetailPage.module.css'

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
  return nodes.map((n, idx) => ({
    key: n.node_key,
    label: n.node_key,
    status: normalizeStatus(n.status),
    x: idx * 140 + 50,
    y: 80,
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

function durationSeconds(
  start?: string | null,
  end?: string | null
): number | undefined {
  if (!start || !end) return undefined
  const s = new Date(start).getTime()
  const e = new Date(end).getTime()
  if (Number.isNaN(s) || Number.isNaN(e)) return undefined
  const diff = Math.round((e - s) / 1000)
  return diff >= 0 ? diff : 0
}

function formatRunDuration(run: NodeRunRecord): string {
  const d = durationSeconds(run.started_at, run.finished_at)
  return typeof d === 'number' ? `${d}s` : '—'
}

function formatRunTime(run: NodeRunRecord): string {
  if (!run.started_at) return '—'
  const date = new Date(run.started_at)
  return Number.isNaN(date.getTime())
    ? run.started_at
    : date.toLocaleString('zh-CN')
}

export default function JobDetailPage() {
  const { workspaceId, jobId } = useParams<{
    workspaceId: string
    jobId: string
  }>()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<JobDetailResponse | null>(null)
  const [error, setError] = useState('')
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)
  const [artifactName, setArtifactName] = useState('')
  const [artifactContent, setArtifactContent] = useState('')
  const artifactRequestId = useRef(0)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDetail(null)
    setError('')
    setSelectedNodeKey(null)
    setArtifactName('')
    setArtifactContent('')
    if (!jobId) return
    let stale = false
    fetchJobDetail(jobId)
      .then((data) => {
        if (stale) return
        setDetail(data)
      })
      .catch((err) => {
        if (stale) return
        setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      stale = true
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
      label: n.node_key,
      status: normalizeStatus(n.status),
      startedAt: n.started_at || undefined,
      endedAt: n.finished_at || undefined,
      duration: durationSeconds(n.started_at, n.finished_at),
      agentId: undefined,
    }
  }, [detail, selectedNodeKey])

  const nodeRuns: NodeRun[] = useMemo(() => {
    if (!detail) return []
    return detail.runs.map((run) => ({
      nodeKey: run.node_key,
      nodeLabel: run.node_key,
      status: JOB_STATUS_LABELS[run.status] || run.status,
      time: formatRunTime(run),
      duration: formatRunDuration(run),
    }))
  }, [detail])

  async function openArtifact(name: string) {
    if (!jobId) return
    const requestId = ++artifactRequestId.current
    setArtifactName(name)
    setArtifactContent('')
    try {
      const artifact = await fetchJobArtifact(jobId, name)
      if (requestId !== artifactRequestId.current) return
      setArtifactContent(artifact.content)
    } catch (err) {
      if (requestId !== artifactRequestId.current) return
      setArtifactContent(err instanceof Error ? err.message : String(err))
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
      <div className={styles.topBar}>
        <button
          type="button"
          className={styles.backBtn}
          onClick={() => navigate(`/workspaces/${workspaceId}/jobs`)}
          data-testid="back-btn"
        >
          ◀ 返回列表
        </button>
        <h1 className={styles.title}>
          {detail?.job.title || detail?.job.source_id || '任务详情'}
        </h1>
        <div className={styles.topActions}>
          <button
            type="button"
            className={styles.actionBtn}
            disabled
            title="即将支持"
            onClick={() => {
              /* TODO rerun */
            }}
          >
            🔄 重跑
          </button>
          <button
            type="button"
            className={styles.actionBtn}
            disabled
            title="即将支持"
            onClick={() => {
              /* TODO run to */
            }}
          >
            ▶️ 运行到...
          </button>
        </div>
      </div>

      {error ? <p className={styles.error}>{error}</p> : null}

      <div className={styles.columns}>
        <div className={styles.left}>
          <div className={styles.graphWrap}>
            <DagGraph
              nodes={dagNodes}
              edges={dagEdges}
              selectedNodeKey={selectedNodeKey}
              onNodeClick={setSelectedNodeKey}
            />
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
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>运行记录</h3>
            <NodeRunsTable runs={nodeRuns} />
          </section>

          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>产物文件</h3>
            {detail && detail.artifacts.length > 0 ? (
              <ul className={styles.artifactList}>
                {detail.artifacts.map((name) => (
                  <li key={name}>
                    <button
                      type="button"
                      className={styles.artifactBtn}
                      onClick={() => openArtifact(name)}
                    >
                      {name}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={styles.empty}>暂无产物</p>
            )}
            {artifactName ? (
              <div className={styles.artifactPreview}>
                <h4 className={styles.artifactName}>{artifactName}</h4>
                <pre className={styles.artifactPre}>{artifactContent}</pre>
              </div>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  )
}
