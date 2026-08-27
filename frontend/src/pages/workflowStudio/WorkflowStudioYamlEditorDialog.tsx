import { Close } from '@mui/icons-material'
import { Dialog, IconButton, Toolbar, Tooltip, Typography } from '@mui/material'
import { useStudioState, useStudioView } from './studioStateContext'
import { WorkflowDefinitionEditor } from './WorkflowDefinitionEditor'
import styles from './WorkflowStudioYamlEditorDialog.module.css'

/** YAML 编辑全屏 Dialog（原画布「YAML」模式下沉）：结构性编辑（新节点/改边/
 * 改 key）的唯一入口，由画布工具栏「编辑 YAML」按钮打开。 */
export function WorkflowStudioYamlEditorDialog() {
  const studio = useStudioState()
  const view = useStudioView()
  return (
    <Dialog
      open={view.yamlEditorOpen}
      onClose={() => view.setYamlEditorOpen(false)}
      fullScreen
      aria-labelledby="workflow-yaml-editor-title"
    >
      <Toolbar className={styles.toolbar}>
        <Typography
          id="workflow-yaml-editor-title"
          variant="h6"
          component="div"
          className={styles.title}
        >
          编辑 YAML
        </Typography>
        <Tooltip title="关闭">
          <IconButton
            edge="end"
            onClick={() => view.setYamlEditorOpen(false)}
            aria-label="close YAML editor"
          >
            <Close />
          </IconButton>
        </Tooltip>
      </Toolbar>
      <div className={styles.body}>
        <WorkflowDefinitionEditor
          value={studio.definitionYaml}
          onChange={studio.setDefinitionYaml}
          readOnly={studio.readOnly}
          label={studio.readOnly ? 'Revision YAML' : '工作流 YAML'}
        />
      </div>
    </Dialog>
  )
}
