import { useState } from 'react'
import {
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  useMediaQuery,
} from '@mui/material'
import { MaterialIcon } from '../MaterialIcon'
import { LabeledIconButton } from '../LabeledIconButton'
import styles from './JobDetailActions.module.css'

export type JobToolbarAction = {
  icon: string
  label: string
  ariaLabel?: string
  tooltip?: string
  disabled?: boolean
  color?: 'error' | 'secondary'
  onClick: () => void
}

export function JobDetailToolbar({
  execution,
  secondary,
  onOpenDiagnosis,
}: {
  execution: JobToolbarAction[]
  secondary: JobToolbarAction[]
  onOpenDiagnosis?: () => void
}) {
  const compact = useMediaQuery('(max-width:1099.95px)')
  const mobile = useMediaQuery('(max-width:759.95px)')
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const menuActions = mobile ? [...execution, ...secondary] : secondary
  return (
    <div className={styles.actions} data-testid="job-detail-actions">
      {!mobile &&
        execution.map((action) => (
          <LabeledIconButton
            key={action.label}
            {...action}
            iconOnly={compact}
          />
        ))}
      <LabeledIconButton
        icon="more_vert"
        label="更多"
        ariaLabel="更多任务操作"
        iconOnly={compact}
        onClick={(event) => setAnchor(event.currentTarget)}
      />
      {onOpenDiagnosis && (
        <LabeledIconButton
          icon="smart_toy"
          label="排查助手"
          tooltip="排查助手（agent 对话，不限于出错节点）"
          onClick={onOpenDiagnosis}
          iconOnly={compact}
        />
      )}
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        slotProps={{ list: { 'aria-label': '任务操作' } }}
      >
        {menuActions.map((action) => (
          <MenuItem
            key={action.label}
            aria-label={action.ariaLabel ?? action.label}
            disabled={action.disabled}
            sx={action.color ? { color: `${action.color}.main` } : undefined}
            onClick={() => {
              setAnchor(null)
              action.onClick()
            }}
          >
            <ListItemIcon sx={{ color: 'inherit' }}>
              <MaterialIcon name={action.icon} />
            </ListItemIcon>
            <ListItemText>{action.label}</ListItemText>
          </MenuItem>
        ))}
      </Menu>
    </div>
  )
}
