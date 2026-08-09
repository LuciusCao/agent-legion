import type { ComponentType } from 'react'
import CodeIcon from '@mui/icons-material/Code'
import DifferenceIcon from '@mui/icons-material/Difference'
import MemoryIcon from '@mui/icons-material/Memory'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import { Button } from '@mui/material'
import styles from './WorkflowStudioCommandBar.module.css'

type Props = {
  onOpenChanges: () => void
  onOpenYaml: () => void
  onOpenAgents: () => void
  onOpenExecutors: () => void
}

export function WorkflowStudioGlobalActions(props: Props) {
  const actions: [onClick: () => void, Icon: ComponentType, label: string][] = [
    [props.onOpenChanges, DifferenceIcon, '查看变更'],
    [props.onOpenYaml, CodeIcon, 'YAML 高级编辑'],
    [props.onOpenAgents, SmartToyIcon, 'Agent 管理'],
    [props.onOpenExecutors, MemoryIcon, 'Executor 管理'],
  ]
  return (
    <div className={styles.globalActions}>
      {actions.map(([onClick, Icon, label]) => (
        <Button key={label} size="small" startIcon={<Icon />} onClick={onClick}>
          <span className={styles.globalActionLabel}>{label}</span>
        </Button>
      ))}
    </div>
  )
}
