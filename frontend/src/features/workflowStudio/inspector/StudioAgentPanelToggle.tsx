import { SmartToy, SmartToyOutlined } from '@mui/icons-material'
import { IconButton, Tooltip } from '@mui/material'

/** Agent 面板开关：出现在画布工具栏与节点详情面包屑右侧。 */
export function StudioAgentPanelToggle(props: {
  open: boolean
  onToggle: () => void
}) {
  return (
    <Tooltip title={props.open ? '收起 Agent 面板' : '展开 Agent 面板'}>
      <IconButton
        size="small"
        onClick={props.onToggle}
        aria-label="toggle agent panel"
        color={props.open ? 'primary' : 'default'}
      >
        {props.open ? <SmartToy /> : <SmartToyOutlined />}
      </IconButton>
    </Tooltip>
  )
}
