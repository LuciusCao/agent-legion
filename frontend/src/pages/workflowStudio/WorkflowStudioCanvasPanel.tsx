import { WorkflowStudioCanvasBody } from './WorkflowStudioCanvasBody'
import { WorkflowStudioCanvasToolbar } from './WorkflowStudioCanvasToolbar'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import canvasStyles from '../WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'
import splitStyles from './WorkflowStudioSplitLayout.module.css'

type Props = {
  props: StudioLayoutProps
  agentOpen: boolean
  onToggleAgent: () => void
  mobileActive: boolean
  replacedByDetail: boolean
}

/** 左半画布（含三模式工具栏）。Agent 面板展开且选中节点时被详情替换
 * （桌面端 display:none，移动端仍由面板切换控制）。 */
export function WorkflowStudioCanvasPanel({
  props,
  agentOpen,
  onToggleAgent,
  mobileActive,
  replacedByDetail,
}: Props) {
  const className = [
    canvasStyles.canvas,
    splitStyles.colLeft,
    mobileActive ? pageStyles.activePanel : '',
    replacedByDetail ? splitStyles.canvasReplaced : '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <main className={className} data-mobile-panel="graph">
      <div data-canvas-toolbar className={canvasToolbarStyles.canvasToolbar}>
        <WorkflowStudioCanvasToolbar
          mode={props.canvasMode}
          onModeChange={props.setCanvasMode}
          agentOpen={agentOpen}
          onToggleAgent={onToggleAgent}
          onDagFullscreen={() => props.setDagFullscreenOpen(true)}
        />
      </div>
      <WorkflowStudioCanvasBody props={props} />
    </main>
  )
}
