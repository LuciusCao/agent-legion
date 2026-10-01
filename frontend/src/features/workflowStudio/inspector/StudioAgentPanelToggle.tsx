import { SmartToy, SmartToyOutlined } from '@mui/icons-material'
import { IconButton, Tooltip } from '@mui/material'
import { useStudioView } from '../shared/studioStateContext'

/** Agent 面板开关：appbar（CommandBar）唯一入口（#668），开合状态读
 * StudioViewContext（useWorkflowStudioPageView）。#797 复审轮 2：展示以
 * dockVisible（窄屏=实际可见性）为准——agentOpen 与页签可能脱节。
 * #799 精修：右岛图标 + 文字并排（文字 span 带 studio-island-text 全局类，
 * 窄屏由岛的 CSS 隐藏、只留图标）。 */
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
        sx={{ borderRadius: '8px', gap: '4px', padding: '4px 8px' }}
      >
        {open ? <SmartToy /> : <SmartToyOutlined />}
        <span className="studio-island-text">Agent 助手</span>
      </IconButton>
    </Tooltip>
  )
}
