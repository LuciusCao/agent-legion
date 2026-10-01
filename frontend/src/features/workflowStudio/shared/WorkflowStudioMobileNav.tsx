import { Tab, Tabs } from '@mui/material'
import type { ReactNode } from 'react'
import styles from './WorkflowStudioMobileNav.module.css'

/** #804 抽屉化后窄屏只剩两页签：节点编辑是全覆盖 Drawer（点节点直接开，
 * 不再有「编辑节点」页签/分栏概念）。trailing = 页签行右端常驻位（轮 4
 * P1-B 的窄屏警示徽标：它必须在页签行而不是画布列里，Agent 页签下画布
 * 列整列隐藏时仍可见）。 */
export type StudioMobilePanel = 'graph' | 'agent'

type Props = {
  value: StudioMobilePanel
  onChange: (value: StudioMobilePanel) => void
  trailing?: ReactNode
}

export function WorkflowStudioMobileNav({ value, onChange, trailing }: Props) {
  return (
    <div className={styles.row} data-testid="studio-mobile-nav-row">
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
      {trailing}
    </div>
  )
}
