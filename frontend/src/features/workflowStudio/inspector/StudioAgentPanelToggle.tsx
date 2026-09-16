import { SmartToy, SmartToyOutlined } from '@mui/icons-material'
import { IconButton, Tooltip } from '@mui/material'
import { useStudioView } from '../shared/studioStateContext'

/** Agent 面板开关：appbar（CommandBar）唯一入口（#668），开合状态读
 * StudioViewContext（useWorkflowStudioPageView）。 */
export function StudioAgentPanelToggle() {
  const view = useStudioView()
  return (
    <Tooltip title={view.agentOpen ? '收起 Agent 面板' : '展开 Agent 面板'}>
      <IconButton
        size="small"
        onClick={view.toggleAgent}
        aria-label="toggle agent panel"
        color={view.agentOpen ? 'primary' : 'default'}
      >
        {view.agentOpen ? <SmartToy /> : <SmartToyOutlined />}
      </IconButton>
    </Tooltip>
  )
}
