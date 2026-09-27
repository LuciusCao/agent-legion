import { SmartToy, SmartToyOutlined } from '@mui/icons-material'
import { IconButton, Tooltip } from '@mui/material'
import { useStudioView } from '../shared/studioStateContext'

/** Agent 面板开关：appbar（CommandBar）唯一入口（#668），开合状态读
 * StudioViewContext（useWorkflowStudioPageView）。#797 复审轮 2：展示以
 * dockVisible（窄屏=实际可见性）为准——agentOpen 与页签可能脱节。 */
export function StudioAgentPanelToggle() {
  const view = useStudioView()
  const open = view.dockVisible
  return (
    <Tooltip title={open ? '收起 Agent 面板' : '展开 Agent 面板'}>
      <IconButton
        size="small"
        onClick={view.toggleAgent}
        aria-label="toggle agent panel"
        color={open ? 'primary' : 'default'}
      >
        {open ? <SmartToy /> : <SmartToyOutlined />}
      </IconButton>
    </Tooltip>
  )
}
