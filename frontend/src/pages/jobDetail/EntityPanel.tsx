/**
 * 左栏实体面板的统一入口（issue #11）：
 * - question：结构化业务面板在上 + 通用产物预览在下；
 * - 其余 source_type：通用产物预览兜底（替代旧的 return null / video 空态）。
 * 结构化面板的 gating 布尔在 Phase 5 挪进 manifest 声明，此处暂保留透传。
 */
import type { JobDetail } from '../../types/jobTypes'
import { QuestionContentPanel } from '../../components/question/QuestionContentPanel'
import { ArtifactPreviewPanel } from '../../components/preview/ArtifactPreviewPanel'

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
  const isQuestion = detail?.job.source_type === 'question'

  return (
    <>
      {isQuestion && (
        <QuestionContentPanel
          key={jobId}
          jobId={jobId}
          keyInfoPreviewable={keyInfoPreviewable}
          possibleErrorsPreviewable={possibleErrorsPreviewable}
          keyInfoReviewAttempted={keyInfoReviewAttempted}
          possibleErrorsReviewAttempted={possibleErrorsReviewAttempted}
        />
      )}
      <ArtifactPreviewPanel key={`${jobId}-artifacts`} jobId={jobId} detail={detail ?? null} />
    </>
  )
}
