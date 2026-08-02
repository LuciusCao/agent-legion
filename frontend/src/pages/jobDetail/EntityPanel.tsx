import type { JobDetail } from '../../types/jobTypes'
import { QuestionContentPanel } from '../../components/question/QuestionContentPanel'
import { VideoContentPanel } from '../../components/VideoContentPanel'

export interface EntityPanelProps {
  detail: JobDetail | null | undefined
  jobId: string
  questionArtifactRefreshKey: string
  comprehensionRefreshKey: string
  keyInfoPreviewable: boolean
  possibleErrorsPreviewable: boolean
  keyInfoReviewAttempted: boolean
  possibleErrorsReviewAttempted: boolean
}

export function EntityPanel({
  detail,
  jobId,
  questionArtifactRefreshKey,
  comprehensionRefreshKey,
  keyInfoPreviewable,
  possibleErrorsPreviewable,
  keyInfoReviewAttempted,
  possibleErrorsReviewAttempted,
}: EntityPanelProps) {
  if (detail?.job.source_type === 'question') {
    return (
      <QuestionContentPanel
        key={jobId}
        jobId={jobId}
        refreshKey={questionArtifactRefreshKey}
        comprehensionRefreshKey={comprehensionRefreshKey}
        keyInfoPreviewable={keyInfoPreviewable}
        possibleErrorsPreviewable={possibleErrorsPreviewable}
        keyInfoReviewAttempted={keyInfoReviewAttempted}
        possibleErrorsReviewAttempted={possibleErrorsReviewAttempted}
      />
    )
  }

  if (
    detail?.job.source_type === 'video' ||
    detail?.job.workflow_key === 'video_knowledge'
  ) {
    return (
      <VideoContentPanel
        key={jobId}
        jobId={jobId}
        refreshKey={detail.job.updated_at}
      />
    )
  }

  return null
}
