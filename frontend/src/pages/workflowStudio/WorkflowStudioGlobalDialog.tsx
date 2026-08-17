import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { useWorkflowStudio } from './useWorkflowStudio'
import type { StudioPanelFocus } from './useWorkflowStudioPageView'
import { WorkflowDefinitionEditor } from './WorkflowDefinitionEditor'
import { WorkflowStudioChangesView } from './WorkflowStudioChangesView'
import { ManagedAgentsPanel } from './WorkflowStudioManagedPanels'
import styles from './WorkflowStudioGlobalDialog.module.css'

export type WorkflowStudioGlobalMode = 'changes' | 'yaml' | 'agents'

type Props = {
  mode: WorkflowStudioGlobalMode | null
  studio: ReturnType<typeof useWorkflowStudio>
  panelFocus: StudioPanelFocus
  onClose: () => void
}

export function WorkflowStudioGlobalDialog(props: Props) {
  const { mode, studio, onClose } = props

  const selectNode = (nodeKey: string) => {
    studio.setSelectedNodeKey(nodeKey)
    onClose()
  }

  if (mode === null) return null

  return (
    <Dialog
      open
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      PaperProps={{ className: styles.paper }}
    >
      <DialogTitle>
        {mode === 'yaml'
          ? 'YAML 高级编辑'
          : mode === 'agents'
            ? 'Agent 管理'
            : '变更与校验'}
      </DialogTitle>
      <DialogContent dividers className={styles.content}>
        {mode === 'agents' && (
          <ManagedAgentsPanel focusId={props.panelFocus.agents} />
        )}
        {mode === 'changes' && (
          <WorkflowStudioChangesView
            studio={studio}
            onSelectNode={selectNode}
          />
        )}
        {mode === 'yaml' && (
          <div className={styles.yamlEditor}>
            <WorkflowDefinitionEditor
              value={studio.definitionYaml}
              onChange={studio.setDefinitionYaml}
              readOnly={studio.readOnly}
              label={studio.readOnly ? 'Revision YAML' : '工作流 YAML'}
            />
          </div>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>关闭</Button>
      </DialogActions>
    </Dialog>
  )
}
