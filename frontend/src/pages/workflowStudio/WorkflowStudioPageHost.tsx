import { AppShell } from '../../layouts/AppShell'
import { WorkflowStudioAppBar } from './WorkflowStudioAppBar'
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
  const view = useWorkflowStudioPageView(studio)

  // Provider 挂在 AppShell 外层：顶栏 CommandBar（AppBar 区域）与页面主体
  // 都消费同一份 studio/view 状态（如状态 chip 点击打开变更面板）。
  return (
    <StudioStateContext.Provider value={studio}>
      <StudioViewContext.Provider value={view}>
        <AppShell
          appBar={({ scrolled }) => (
            <WorkflowStudioAppBar
              workspaceId={workspaceId}
              scrolled={scrolled}
            />
          )}
        >
          <WorkflowStudioPageContent studio={studio} />
        </AppShell>
      </StudioViewContext.Provider>
    </StudioStateContext.Provider>
  )
}
