import { WorkflowCatalogLoadError } from './WorkflowCatalogLoadError'
import { WorkflowStudioEmptyGuide } from '../canvas/WorkflowStudioEmptyGuide'
import { WorkflowStudioMobileNav } from './WorkflowStudioMobileNav'
import { WorkflowStudioSplitLayout } from './WorkflowStudioSplitLayout'
import { useStudioState, useStudioView } from './studioStateContext'
import islandStyles from './StudioCanvasIslands.module.css'

/** 工作区主体：画布永远全宽（#804 抽屉化——节点详情改右侧 Drawer 浮层，
 * 分栏退役）；Agent 对话默认展开、浮层 Dock 承载（#795 PR②，不占 grid
 * 轨道）。移动端退化为 画布/Agent 两页签切换——Agent 页签唤起 Dock 浮层，
 * 点节点直接开全覆盖抽屉。
 * mobilePanel/agentOpen 都在 StudioViewContext（useWorkflowStudioPageView，
 * #797 codex 复审轮：toggleAgent 是开合的唯一组合出口，窄屏页签同步在
 * 那层组合，顶栏开关与 Dock 关闭按钮走同一条路）。 */
export function WorkflowStudioWorkspace() {
  const studio = useStudioState()
  const view = useStudioView()

  return (
    <>
      {studio.loadState === 'empty' && <WorkflowStudioEmptyGuide />}
      {studio.agentCatalogError && (
        <WorkflowCatalogLoadError onRetry={studio.retryAgentCatalog} />
      )}
      {/* #799：scope 提供岛的绝对定位锚点；岛在 SplitLayout 之后渲染，
          同 z 档（900）下靠 DOM 序盖住画布、被 Dock（Portal 更后）压。 */}
      <div className={islandStyles.scope}>
        <WorkflowStudioMobileNav
          value={view.mobilePanel}
          onChange={(next) => {
            view.setMobilePanel(next)
            // 移动端 Agent 页签 = 唤起 Dock（chat 已在浮层，不占面板位）。
            if (next === 'agent' && !view.agentOpen) view.toggleAgent()
          }}
        />
        <WorkflowStudioSplitLayout
          mobilePanel={view.mobilePanel}
          agentOpen={view.agentOpen}
        />
      </div>
    </>
  )
}
