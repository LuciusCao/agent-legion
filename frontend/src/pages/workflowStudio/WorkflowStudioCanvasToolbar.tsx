import { Button } from '@mui/material'
import { StudioAgentPanelToggle } from './StudioAgentPanelToggle'
import { WorkflowDagFullscreenButton } from './components/WorkflowDagFullscreenButton'

type Props = {
  agentOpen: boolean
  onToggleAgent: () => void
  onEditYaml: () => void
  onDagFullscreen: () => void
}

/** 画布工具栏：Agent 面板开关 + 编辑 YAML（打开全屏 Dialog）+ DAG 全屏。
 * DAG 是唯一常驻画布视图，不再有模式切换。 */
export function WorkflowStudioCanvasToolbar(props: Props) {
  return (
    <>
      <StudioAgentPanelToggle
        open={props.agentOpen}
        onToggle={props.onToggleAgent}
      />
      <Button size="small" onClick={props.onEditYaml}>
        编辑 YAML
      </Button>
      <WorkflowDagFullscreenButton onClick={props.onDagFullscreen} />
    </>
  )
}
