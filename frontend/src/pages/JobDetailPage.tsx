import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { JobProgressPanel } from '../components/job/JobProgressPanel'
import { fetchJobArtifact } from '../api'
import { deriveJobDetailPresentation } from './jobDetail/deriveJobDetailPresentation'
import styles from './JobDetailPage.module.css'
import { ArtifactListDialog } from '../components/artifact/ArtifactListDialog'
import { ArtifactPreviewDialog } from '../components/artifact/ArtifactPreviewDialog'
import { useJobApprovalGate } from './jobDetail/useJobApprovalGate'
import { DagFullscreenDialog } from '../components/dag/DagFullscreenDialog'
import { TokenUsageDialog } from '../components/tokenUsage/TokenUsageDialog'
import { NonUploadableNotice } from '../components/job/NonUploadableNotice'
import { useJobDetail } from './jobDetail/useJobDetail'
import { useJobDetailActions } from './jobDetail/useJobDetailActions'
import { EntityPanel } from './jobDetail/EntityPanel'

export default function JobDetailPage() {
  const { workspaceId, jobId } = useParams<{
    workspaceId: string
    jobId: string
  }>()
  const {
    detail,
    error,
    actionLoading,
    handleRerun,
    handleRunTo,
    handleApproval,
    handleContinue,
    handleUpgradeWorkflow,
    handlePackage,
    handleClearPacked,
    handleDelete,
  } = useJobDetail(workspaceId, jobId)
  const [artifactListOpen, setArtifactListOpen] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewArtifact, setPreviewArtifact] = useState<{
    name: string
    content: string
  } | null>(null)
  const [dagDialogOpen, setDagDialogOpen] = useState(false)
  const artifactRequestId = useRef(0)

  useEffect(() => {
    const reset = () => {
      setArtifactListOpen(false)
      setPreviewOpen(false)
      setPreviewArtifact(null)
    }
    reset()
    return reset
  }, [jobId])

  const { dagNodes, dagEdges, nodeCatalog } = useMemo(
    () => deriveJobDetailPresentation(detail),
    [detail]
  )

  const openArtifact = useCallback(
    async (name: string) => {
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
    },
    [jobId]
  )

  const openArtifactList = useCallback(() => setArtifactListOpen(true), [])

  // prettier-ignore
  const { openApproval, approvalDialog } = useJobApprovalGate(workspaceId, jobId, detail, actionLoading, handleApproval, openArtifact)

  useJobDetailActions({
    detail,
    nodeCatalog,
    actionLoading,
    onRerun: handleRerun,
    onRunTo: handleRunTo,
    onContinue: handleContinue,
    onUpgradeWorkflow: handleUpgradeWorkflow,
    onPackage: handlePackage,
    onClearPacked: handleClearPacked,
    onDelete: handleDelete,
    onOpenArtifacts: openArtifactList,
    onOpenApproval: openApproval,
  })

  if (!jobId) {
    return <p className="error-text">缺少任务 ID</p>
  }

  if (!detail && !error) {
    return <p className={styles.loading}>加载中...</p>
  }

  return (
    <div className={styles.page}>
      {error ? <p className={styles.error}>{error}</p> : null}

      {detail && jobId && (
        <NonUploadableNotice
          jobId={jobId}
          jobStatus={detail.job.status}
          artifacts={detail.artifacts}
        />
      )}

      <div className={styles.columns}>
        <div className={styles.left}>
          {jobId && (
            <EntityPanel
              detail={detail}
              jobId={jobId}
              workspaceId={workspaceId}
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
      {approvalDialog}
      <DagFullscreenDialog
        open={dagDialogOpen}
        jobId={jobId}
        nodes={dagNodes}
        edges={dagEdges}
        runs={detail?.runs}
        onClose={() => setDagDialogOpen(false)}
      />
      {jobId && <TokenUsageDialog scope="job" jobId={jobId} />}
    </div>
  )
}
