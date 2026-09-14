/**
 * 左栏实体面板的统一入口（issue #11 + #328）：
 * - workspace 有已发布预览面板 bundle → PreviewPanelSection 用沙箱 bundle
 *   host 接管整栏（定制面板经只读桥自取产物）；
 * - 无定制 → 回落现有结构：question 走内置 bundle（QuestionContentPanel），
 *   其余 source_type 由通用产物预览兜底（扩展名分发不变）。
 * 「定制预览」按钮在 PreviewPanelSection 头部。
 * #255：question 任务的通用预览收到结构化面板已消费的产物名单
 * （QUESTION_CONSUMED_ARTIFACTS），同名原始卡片默认去重；其余实体无
 * 名单，行为不变。
 */
import type { JobDetail } from '../../types/jobTypes'
import { QuestionContentPanel } from '../../components/question/QuestionContentPanel'
import { ArtifactPreviewPanel } from '../../components/preview/ArtifactPreviewPanel'
import { PreviewPanelSection } from '../../features/previewPanel/PreviewPanelSection'
import { QUESTION_CONSUMED_ARTIFACTS } from './questionPreviewManifest'

export interface EntityPanelProps {
  detail: JobDetail | null | undefined
  jobId: string
  workspaceId?: string
}

export function EntityPanel({ detail, jobId, workspaceId }: EntityPanelProps) {
  const isQuestion = detail?.job.source_type === 'question'

  const fallback = (
    <>
      {isQuestion && <QuestionContentPanel key={jobId} jobId={jobId} />}
      <ArtifactPreviewPanel
        key={`${jobId}-artifacts`}
        jobId={jobId}
        detail={detail ?? null}
        workspaceId={workspaceId}
        structuredHidden={isQuestion ? QUESTION_CONSUMED_ARTIFACTS : undefined}
      />
    </>
  )

  return (
    <PreviewPanelSection
      jobId={jobId}
      workspaceId={workspaceId}
      fallback={fallback}
    />
  )
}
