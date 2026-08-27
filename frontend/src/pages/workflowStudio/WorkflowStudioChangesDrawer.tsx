import { Close } from '@mui/icons-material'
import { Drawer, IconButton, Tooltip, Typography } from '@mui/material'
import { useStudioState, useStudioView } from './studioStateContext'
import { WorkflowStudioChangesView } from './WorkflowStudioChangesView'
import styles from './WorkflowStudioChangesDrawer.module.css'

/** 变更面板：右侧 Drawer，承载校验结果与草稿对比（原画布「变更」模式下沉）。
 * 打开入口：顶栏状态 chip 点击、校验完成、发布前 review。 */
export function WorkflowStudioChangesDrawer() {
  const studio = useStudioState()
  const view = useStudioView()
  return (
    <Drawer
      anchor="right"
      open={view.changesPanelOpen}
      onClose={() => view.setChangesPanelOpen(false)}
    >
      <div className={styles.panel}>
        <div className={styles.header}>
          <Typography variant="h6" component="div" className={styles.title}>
            变更与校验
          </Typography>
          <Tooltip title="关闭">
            <IconButton
              edge="end"
              onClick={() => view.setChangesPanelOpen(false)}
              aria-label="close changes panel"
            >
              <Close />
            </IconButton>
          </Tooltip>
        </div>
        <WorkflowStudioChangesView
          studio={studio}
          onSelectNode={(nodeKey) => {
            studio.setSelectedNodeKey(nodeKey)
            view.setChangesPanelOpen(false)
          }}
        />
      </div>
    </Drawer>
  )
}
