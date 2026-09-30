import { Tab, Tabs } from '@mui/material'
import styles from './WorkflowStudioMobileNav.module.css'

/** #804 抽屉化后窄屏只剩两页签：节点编辑是全覆盖 Drawer（点节点直接开，
 * 不再有「编辑节点」页签/分栏概念）。 */
export type StudioMobilePanel = 'graph' | 'agent'

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
      data-testid="studio-mobile-nav"
    >
      <Tab value="graph" label="画布" />
      <Tab value="agent" label="Agent" />
    </Tabs>
  )
}
