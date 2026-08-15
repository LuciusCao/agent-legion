import { useMemo } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'
import type { useWorkflowStudioPageView } from './useWorkflowStudioPageView'
import { StudioNavContext, type StudioNav } from './workflowStudioNav'
import { WorkflowStudioGlobalDialog } from './WorkflowStudioGlobalDialog'
import { WorkflowStudioLayout } from './WorkflowStudioLayout'

type Studio = ReturnType<typeof useWorkflowStudio>
type View = ReturnType<typeof useWorkflowStudioPageView>

export function WorkflowStudioPageContent(props: {
  studio: Studio
  view: View
}) {
  const { studio, view } = props
  // useMemo 稳住 context value：YAML 击键会重渲染本组件，新建的 nav 对象
  // 会让全部 useStudioNav 消费者无谓重渲染。
  const nav: StudioNav = useMemo(
    () => ({
      openAgent: (agentId) => view.openPanel('agents', agentId),
      openExecutor: (executorId) => view.openPanel('executors', executorId),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只依赖稳定化后的 openPanel
    [view.openPanel]
  )
  return (
    <StudioNavContext.Provider value={nav}>
      <WorkflowStudioLayout
        {...studio}
        dagFullscreenOpen={view.dagFullscreenOpen}
        setDagFullscreenOpen={view.setDagFullscreenOpen}
        onValidate={() => void studio.validateDraft()}
        onPublish={() => void studio.requestPublish()}
        onReset={studio.resetDefinition}
        onShowChanges={() => view.setGlobalMode('changes')}
      />
      <WorkflowStudioGlobalDialog
        mode={view.globalMode}
        studio={studio}
        panelFocus={view.panelFocus}
        onClose={() => view.setGlobalMode(null)}
      />
    </StudioNavContext.Provider>
  )
}
