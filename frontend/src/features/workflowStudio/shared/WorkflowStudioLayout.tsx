import { WorkflowStudioLayoutDialogs } from './WorkflowStudioLayoutDialogs'
import { WorkflowStudioWorkspace } from './WorkflowStudioWorkspace'
import { StudioCanvasBackIsland } from './StudioCanvasBackIsland'
import { useStudioState } from './studioStateContext'
import basePageStyles from '../../../pages/WorkflowStudioPage.module.css'
import islandStyles from './StudioCanvasIslands.module.css'

export function WorkflowStudioLayout() {
  const studio = useStudioState()
  return (
    <>
      <div className={basePageStyles.page}>
        {/* #799 codex 复核 P2：加载/失败态不挂 Workspace、双岛不渲染——
            AppBar 已移除，返回入口经最小返回岛常驻。 */}
        {(studio.loadState === 'loading' || studio.loadState === 'error') && (
          <div className={islandStyles.scope}>
            <StudioCanvasBackIsland />
          </div>
        )}
        {studio.loadState === 'loading' && <p>正在加载 workflow</p>}
        {studio.loadState === 'error' && (
          <p>无法加载 active workflow revision</p>
        )}
        {(studio.loadState === 'ready' || studio.loadState === 'empty') && (
          <WorkflowStudioWorkspace />
        )}
      </div>
      <WorkflowStudioLayoutDialogs />
    </>
  )
}
