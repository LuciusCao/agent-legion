import { AppShell } from '../../../layouts/AppShell'
import { WorkflowStudioPageContent } from './WorkflowStudioPageContent'
import { StudioStateContext, StudioViewContext } from './studioStateContext'
import { useWorkflowStudio } from './useWorkflowStudio'
import { useWorkflowStudioPageView } from './useWorkflowStudioPageView'

export function WorkflowStudioPageHost({
  workspaceId,
}: {
  workspaceId?: string
}) {
  const studio = useWorkflowStudio(workspaceId)
  const view = useWorkflowStudioPageView(workspaceId)

  // Provider 挂在 AppShell 外层：浮动功能岛（#799，原 AppBar 区域）与页面
  // 主体都消费同一份 studio/view 状态（如状态 chip 点击打开变更面板）。
  // #799：studio 去 AppBar 画布化——不传 appBar，AppShell 不渲染顶栏区，
  // 原顶栏内容拆为 DAG 上的浮动功能岛（StudioCanvasIslands）。
  return (
    <StudioStateContext.Provider value={studio}>
      <StudioViewContext.Provider value={view}>
        <AppShell>
          <WorkflowStudioPageContent studio={studio} />
        </AppShell>
      </StudioViewContext.Provider>
    </StudioStateContext.Provider>
  )
}
