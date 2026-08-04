import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { JobProgressPanel } from '../components/job/JobProgressPanel'
import { fetchJobArtifact } from '../api'
import { usePageHeaderStore } from '../stores/pageHeaderStore'
import { deriveJobDetailPresentation } from './jobDetail/deriveJobDetailPresentation'
import styles from './JobDetailPage.module.css'
import { ArtifactListDialog } from '../components/artifact/ArtifactListDialog'
import { ArtifactPreviewDialog } from '../components/artifact/ArtifactPreviewDialog'
import { DagFullscreenDialog } from '../components/dag/DagFullscreenDialog'
import { TokenUsageDialog } from '../components/tokenUsage/TokenUsageDialog'
import { JobDetailActions } from '../components/job/JobDetailActions'
import { useJobDetail } from './jobDetail/useJobDetail'
import { EntityPanel } from './jobDetail/EntityPanel'

export default function JobDetailPage() {
  const { workspaceId, jobId } = useParams<{
    workspaceId: string
    jobId: string
  }>()
  const { setDetailPageActions } = usePageHeaderStore()
  const {
    detail,
    error,
    actionLoading,
    handleRerun,
    handleRunTo,
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

  const {
    dagNodes,
    dagEdges,
    workflowDefinition,
    questionArtifactRefreshKey,
    comprehensionRefreshKey,
    keyInfoPreviewable,
    possibleErrorsPreviewable,
    keyInfoReviewAttempted,
    possibleErrorsReviewAttempted,
  } = useMemo(() => deriveJobDetailPresentation(detail), [detail])

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
        onUpgradeWorkflow={handleUpgradeWorkflow}
        onPackage={handlePackage}
        onClearPacked={handleClearPacked}
        onDelete={handleDelete}
        onOpenArtifacts={() => setArtifactListOpen(true)}
      />
    )
    return () => setDetailPageActions(null)
  }, [
    detail,
    setDetailPageActions,
    workflowDefinition,
    actionLoading,
    handleRerun,
    handleRunTo,
    handleContinue,
    handleUpgradeWorkflow,
    handlePackage,
    handleClearPacked,
    handleDelete,
  ])

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
          {jobId && (
            <EntityPanel
              detail={detail}
              jobId={jobId}
              questionArtifactRefreshKey={questionArtifactRefreshKey}
              comprehensionRefreshKey={comprehensionRefreshKey}
              keyInfoPreviewable={keyInfoPreviewable}
              possibleErrorsPreviewable={possibleErrorsPreviewable}
              keyInfoReviewAttempted={keyInfoReviewAttempted}
              possibleErrorsReviewAttempted={possibleErrorsReviewAttempted}
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
      {jobId && <TokenUsageDialog scope="job" jobId={jobId} />}
    </div>
  )
}
