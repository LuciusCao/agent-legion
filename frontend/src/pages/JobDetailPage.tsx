import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { JobProgressPanel } from '../components/JobProgressPanel'
import { QuestionContentPanel } from '../components/QuestionContentPanel'
import { fetchJobArtifact } from '../api'
import { useUiStore } from '../stores/uiStore'
import { deriveJobDetailPresentation } from './jobDetail/jobNodeHelpers'
import styles from './JobDetailPage.module.css'
import { ArtifactListDialog } from '../components/ArtifactListDialog'
import { ArtifactPreviewDialog } from '../components/ArtifactPreviewDialog'
import { DagFullscreenDialog } from '../components/DagFullscreenDialog'
import { JobDetailActions } from '../components/JobDetailActions'
import { useJobDetail } from './jobDetail/useJobDetail'

export default function JobDetailPage() {
  const { workspaceId, jobId } = useParams<{
    workspaceId: string
    jobId: string
  }>()
  const { setDetailPageActions } = useUiStore()
  const {
    detail,
    error,
    actionLoading,
    handleRerun,
    handleRunTo,
    handleContinue,
    handlePackage,
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
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setArtifactListOpen(false)
    setPreviewOpen(false)
    setPreviewArtifact(null)
    return () => {
      setArtifactListOpen(false)
      setPreviewOpen(false)
      setPreviewArtifact(null)
    }
  }, [jobId])

  const {
    dagNodes,
    dagEdges,
    workflowDefinition,
    questionArtifactRefreshKey,
    comprehensionRefreshKey,
    comprehensionCompleted,
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
              comprehensionRefreshKey={comprehensionRefreshKey}
              comprehensionCompleted={comprehensionCompleted}
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
