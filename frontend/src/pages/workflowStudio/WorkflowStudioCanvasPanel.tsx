import { WorkflowStudioCanvasBody } from './WorkflowStudioCanvasBody'
import { WorkflowStudioCanvasToolbar } from './WorkflowStudioCanvasToolbar'
import { useStudioView } from './studioStateContext'
import canvasStyles from '../WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'
import splitStyles from './WorkflowStudioSplitLayout.module.css'

type Props = {
  agentOpen: boolean
  onToggleAgent: () => void
  mobileActive: boolean
  replacedByDetail: boolean
}

/** 左半画布（DAG 常驻，工具栏含 Agent 面板开关 / 编辑 YAML / DAG 全屏）。
 * Agent 面板展开且选中节点时被详情替换
 * （桌面端 display:none，移动端仍由面板切换控制）。 */
export function WorkflowStudioCanvasPanel({
  agentOpen,
  onToggleAgent,
  mobileActive,
  replacedByDetail,
}: Props) {
  const view = useStudioView()
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
          agentOpen={agentOpen}
          onToggleAgent={onToggleAgent}
          onEditYaml={() => view.setYamlEditorOpen(true)}
          onDagFullscreen={() => view.setDagFullscreenOpen(true)}
        />
      </div>
      <WorkflowStudioCanvasBody />
    </main>
  )
}
