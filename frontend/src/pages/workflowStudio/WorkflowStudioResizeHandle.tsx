import styles from './WorkflowStudioResizeHandle.module.css'
import splitStyles from './WorkflowStudioSplitLayout.module.css'
import { useStudioRightPanelWidth } from './useStudioRightPanelWidth'

/** 右栏左缘的拖拽分隔条：拖动调宽（持久化到 localStorage），双击复位 1:1。 */
export function WorkflowStudioResizeHandle() {
  const { startDrag, resetWidth } = useStudioRightPanelWidth()
  return (
    <div
      className={`${styles.resizeHandle} ${splitStyles.colHandle}`}
      role="separator"
      aria-orientation="vertical"
      aria-label="调整侧栏宽度"
      title="拖拽调整侧栏宽度，双击复位"
      onPointerDown={startDrag}
      onDoubleClick={resetWidth}
    />
  )
}
