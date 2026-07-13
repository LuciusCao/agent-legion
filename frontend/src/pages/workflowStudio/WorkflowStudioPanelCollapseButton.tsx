import { IconButton, Tooltip } from '@mui/material'
import collapseStyles from './WorkflowStudioSidePanels.module.css'

type Props = {
  label: string
  icon: React.ReactNode
  onClick: () => void
}

export function WorkflowStudioPanelCollapseButton(props: Props) {
  return (
    <Tooltip title={props.label}>
      <IconButton
        size="small"
        className={collapseStyles.collapseButton}
        aria-label={props.label}
        onClick={props.onClick}
      >
        {props.icon}
      </IconButton>
    </Tooltip>
  )
}
