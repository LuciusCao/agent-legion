/**
 * 左栏实体面板的统一入口（issue #11）：
 * - question：结构化业务面板在上 + 通用产物预览在下；
 * - 其余 source_type：通用产物预览兜底（替代旧的 return null / video 空态）。
 * 结构化面板的 gating 由 questionPreviewManifest 声明并在面板内部求值。
 */
import type { JobDetail } from '../../types/jobTypes'
import { QuestionContentPanel } from '../../components/question/QuestionContentPanel'
import { ArtifactPreviewPanel } from '../../components/preview/ArtifactPreviewPanel'

export interface EntityPanelProps {
  detail: JobDetail | null | undefined
  jobId: string
}

export function EntityPanel({ detail, jobId }: EntityPanelProps) {
  const isQuestion = detail?.job.source_type === 'question'

  return (
    <>
      {isQuestion && <QuestionContentPanel key={jobId} jobId={jobId} />}
      <ArtifactPreviewPanel key={`${jobId}-artifacts`} jobId={jobId} detail={detail ?? null} />
    </>
  )
}
