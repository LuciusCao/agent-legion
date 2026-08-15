import type { JobDetail } from '../../types/jobTypes'
import { QuestionContentPanel } from '../../components/question/QuestionContentPanel'
import { VideoContentPanel } from '../../components/VideoContentPanel'

export interface EntityPanelProps {
  detail: JobDetail | null | undefined
  jobId: string
  keyInfoPreviewable: boolean
  possibleErrorsPreviewable: boolean
  keyInfoReviewAttempted: boolean
  possibleErrorsReviewAttempted: boolean
}

export function EntityPanel({
  detail,
  jobId,
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
        keyInfoPreviewable={keyInfoPreviewable}
        possibleErrorsPreviewable={possibleErrorsPreviewable}
        keyInfoReviewAttempted={keyInfoReviewAttempted}
        possibleErrorsReviewAttempted={possibleErrorsReviewAttempted}
      />
    )
  }

  if (detail?.job.source_type === 'video') {
    return <VideoContentPanel key={jobId} jobId={jobId} />
  }

  return null
}
