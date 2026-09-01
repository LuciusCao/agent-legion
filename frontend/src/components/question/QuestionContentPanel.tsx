/**
 * question 实体的左栏内容面板（issue #328 改造）：官方内置预览面板 bundle
 * 的宿主。原先的硬编码 React 实现（题干/审题信息/选项/答案/易错点/解析 +
 * 评审徽标）整体迁入 features/previewPanel/builtin/questionPanel.html——
 * 单文件 HTML+CSS+JS，经只读桥取数，与「用户定制 bundle」走同一机制，
 * 前端不再写死业务面板。
 */
import { PreviewPanelHost } from '../../features/previewPanel/PreviewPanelHost'
import { QUESTION_PANEL_BUNDLE } from '../../features/previewPanel/builtin/questionPanelBundle'

export interface QuestionContentPanelProps {
  jobId: string
}

export function QuestionContentPanel({ jobId }: QuestionContentPanelProps) {
  return (
    <PreviewPanelHost
      jobId={jobId}
      html={QUESTION_PANEL_BUNDLE}
      title="题目内容"
    />
  )
}
