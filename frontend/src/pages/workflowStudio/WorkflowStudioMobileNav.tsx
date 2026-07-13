import { Tab, Tabs } from '@mui/material'
import styles from './WorkflowStudioMobileNav.module.css'

export type StudioMobilePanel =
  | 'versions'
  | 'graph'
  | 'inspector'
  | 'changes'
  | 'yaml'

type Props = {
  value: StudioMobilePanel
  onChange: (value: StudioMobilePanel) => void
}

export function WorkflowStudioMobileNav({ value, onChange }: Props) {
  return (
    <Tabs
      value={value}
      onChange={(_, next: StudioMobilePanel) => onChange(next)}
      aria-label="Workflow studio panels"
      variant="scrollable"
      scrollButtons="auto"
      className={styles.nav}
    >
      <Tab value="versions" label="Outline" />
      <Tab value="graph" label="Graph" />
      <Tab value="inspector" label="Inspector" />
      <Tab value="changes" label="Changes" />
      <Tab value="yaml" label="YAML" />
    </Tabs>
  )
}
