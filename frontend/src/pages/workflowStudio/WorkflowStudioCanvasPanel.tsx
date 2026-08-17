import { DagGraph } from '../../components/dag/DagGraph'
import type { DagGraphEdge, DagGraphNode } from '../../components/dag/DagGraph'
import type { WorkflowDefinitionRecord } from '../../types'
import { WorkflowDagFullscreenButton } from './components/WorkflowDagFullscreenButton'
import { StudioAgentPanelToggle } from './StudioAgentPanelToggle'
import canvasStyles from '../WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'
import splitStyles from './WorkflowStudioSplitLayout.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  nodes: DagGraphNode[]
  edges: DagGraphEdge[]
  selectedNodeKey: string | null
  onSelectedNodeChange: (key: string | null) => void
  onDagFullscreen: () => void
  agentOpen: boolean
  onToggleAgent: () => void
  mobileActive: boolean
  replacedByDetail: boolean
}

/** 左半 DAG 画布（含工具栏）。Agent 面板展开且选中节点时被详情替换
 * （桌面端 display:none，移动端仍由面板切换控制）。 */
export function WorkflowStudioCanvasPanel(props: Props) {
  const className = [
    canvasStyles.canvas,
    splitStyles.colLeft,
    props.mobileActive ? pageStyles.activePanel : '',
    props.replacedByDetail ? splitStyles.canvasReplaced : '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <main className={className} data-mobile-panel="graph">
      <div data-canvas-toolbar className={canvasToolbarStyles.canvasToolbar}>
        <StudioAgentPanelToggle
          open={props.agentOpen}
          onToggle={props.onToggleAgent}
        />
        <WorkflowDagFullscreenButton onClick={props.onDagFullscreen} />
      </div>
      {props.workflow && (
        <DagGraph
          nodes={props.nodes}
          edges={props.edges}
          selectedNode={props.selectedNodeKey}
          onSelectedNodeChange={props.onSelectedNodeChange}
          hideNodeDetails
        />
      )}
    </main>
  )
}
