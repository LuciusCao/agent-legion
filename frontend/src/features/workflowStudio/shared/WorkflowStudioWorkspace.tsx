import { WorkflowCatalogLoadError } from './WorkflowCatalogLoadError'
import { WorkflowStudioEmptyGuide } from '../canvas/WorkflowStudioEmptyGuide'
import { WorkflowStudioMobileNav } from './WorkflowStudioMobileNav'
import { WorkflowStudioSplitLayout } from './WorkflowStudioSplitLayout'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'
import { useStudioNarrowViewport } from './useStudioNarrowViewport'
import { useStudioState, useStudioView } from './studioStateContext'

/** 左右分栏入口：Agent 对话默认展开、浮层 Dock 承载（#795 PR②，不占
 * grid 轨道，DAG 区全宽）；点节点时详情固定占右半。移动端退化为
 * 画布/编辑节点/Agent 三面板切换——Agent 页签唤起 Dock 浮层；窄屏关闭
 * Dock 时页签回画布（不留空白工作区，#797 codex P2）。
 * agentOpen 读 StudioViewContext（appbar 开关的唯一状态源，#668）。 */
export function WorkflowStudioWorkspace() {
  const studio = useStudioState()
  const view = useStudioView()
  const { mobilePanel, setMobilePanel } = useWorkflowStudioMobilePanel(
    studio.selectedNodeKey,
    studio.focusNonce
  )
  const narrow = useStudioNarrowViewport()

  return (
    <>
      {studio.loadState === 'empty' && <WorkflowStudioEmptyGuide />}
      {studio.agentCatalogError && (
        <WorkflowCatalogLoadError onRetry={studio.retryAgentCatalog} />
      )}
      <WorkflowStudioMobileNav
        value={mobilePanel}
        editorAvailable={studio.selectedNodeKey !== null}
        onChange={(next) => {
          setMobilePanel(next)
          // 移动端 Agent 页签 = 唤起 Dock（chat 已在浮层，不占面板位）。
          if (next === 'agent' && !view.agentOpen) view.toggleAgent()
        }}
      />
      <WorkflowStudioSplitLayout
        mobilePanel={mobilePanel}
        agentOpen={view.agentOpen}
        onCloseAgent={() => {
          if (view.agentOpen) view.toggleAgent()
          // 窄屏从 Agent 页签关闭 Dock：页签回画布（宽屏 mobilePanel 是
          // 惰性状态，不动）。
          if (narrow && mobilePanel === 'agent') setMobilePanel('graph')
        }}
      />
    </>
  )
}
