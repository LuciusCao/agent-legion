import { Tab, Tabs } from '@mui/material'
import styles from './WorkflowStudioMobileNav.module.css'

export type StudioMobilePanel = 'graph' | 'editor'

type Props = {
  value: StudioMobilePanel
  editorAvailable: boolean
  onChange: (value: StudioMobilePanel) => void
}

export function WorkflowStudioMobileNav({
  value,
  editorAvailable,
  onChange,
}: Props) {
  return (
    <Tabs
      value={value}
      onChange={(_, next: StudioMobilePanel) => onChange(next)}
      aria-label="Workflow studio panels"
      variant="scrollable"
      scrollButtons="auto"
      className={styles.nav}
    >
      <Tab value="graph" label="画布" />
      <Tab value="editor" label="编辑节点" disabled={!editorAvailable} />
    </Tabs>
  )
}
