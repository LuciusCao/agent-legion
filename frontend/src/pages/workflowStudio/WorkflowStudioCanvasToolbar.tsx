import { ToggleButton, ToggleButtonGroup } from '@mui/material'
import type { StudioCanvasMode } from './useWorkflowStudioPageView'
import { StudioAgentPanelToggle } from './StudioAgentPanelToggle'
import { WorkflowDagFullscreenButton } from './components/WorkflowDagFullscreenButton'

type Props = {
  mode: StudioCanvasMode
  onModeChange: (mode: StudioCanvasMode) => void
  agentOpen: boolean
  onToggleAgent: () => void
  onDagFullscreen: () => void
}

/** 画布工具栏：DAG 画布 | YAML | 变更 三模式切换 + Agent 面板开关 + DAG 全屏。 */
export function WorkflowStudioCanvasToolbar(props: Props) {
  return (
    <>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={props.mode}
        onChange={(_event, value: StudioCanvasMode | null) => {
          // exclusive 模式下点当前项会给出 null，保持原模式不变。
          if (value) props.onModeChange(value)
        }}
        aria-label="画布模式"
      >
        <ToggleButton value="dag">DAG 画布</ToggleButton>
        <ToggleButton value="yaml">YAML</ToggleButton>
        <ToggleButton value="changes">变更</ToggleButton>
      </ToggleButtonGroup>
      <StudioAgentPanelToggle
        open={props.agentOpen}
        onToggle={props.onToggleAgent}
      />
      {props.mode === 'dag' && (
        <WorkflowDagFullscreenButton onClick={props.onDagFullscreen} />
      )}
    </>
  )
}
