import {
  StudioRightPanelBody,
  type WorkflowStudioRightPanelProps,
} from './chat/StudioRightPanelBody'
import { StudioRightPanelTabs } from './chat/StudioRightPanelTabs'
import styles from './WorkflowStudioRightPanel.module.css'

export type { WorkflowStudioRightPanelProps }

export function WorkflowStudioRightPanel(props: WorkflowStudioRightPanelProps) {
  return (
    <section className={styles.panel} aria-label="Studio 侧栏">
      <StudioRightPanelTabs
        value={props.activeTab}
        onChange={props.onTabChange}
        onClose={props.onClose}
      />
      <div className={styles.body}>
        <StudioRightPanelBody {...props} />
      </div>
    </section>
  )
}
