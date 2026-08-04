import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { useWorkflowStudio } from './useWorkflowStudio'
import { WorkflowChangeSummaryPanel } from './components/WorkflowChangeSummaryPanel'
import { WorkflowDefinitionEditor } from './WorkflowDefinitionEditor'
import { WorkflowValidationPanel } from './WorkflowValidationPanel'
import styles from './WorkflowStudioGlobalDialog.module.css'

export type WorkflowStudioGlobalMode = 'changes' | 'yaml'

type Props = {
  mode: WorkflowStudioGlobalMode | null
  studio: ReturnType<typeof useWorkflowStudio>
  onClose: () => void
}

export function WorkflowStudioGlobalDialog({ mode, studio, onClose }: Props) {
  const hasValidation =
    studio.validationMessage !== '' ||
    studio.validationErrors.length > 0 ||
    (studio.compareErrors?.length ?? 0) > 0

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
        {mode === 'yaml' ? 'YAML 高级编辑' : '变更与校验'}
      </DialogTitle>
      <DialogContent dividers className={styles.content}>
        {mode === 'changes' && (
          <div className={styles.checks}>
            <section aria-label="校验结果">
              <h3>校验结果</h3>
              {hasValidation ? (
                <WorkflowValidationPanel
                  message={studio.validationMessage}
                  errors={studio.validationErrors}
                  compareErrors={studio.compareErrors ?? undefined}
                  onSelectNode={selectNode}
                />
              ) : (
                <p className={styles.empty}>尚未运行校验。</p>
              )}
            </section>
            <WorkflowChangeSummaryPanel
              summary={studio.compareSummary}
              loading={studio.compareState === 'loading'}
              errors={studio.compareErrors}
              onSelectNode={selectNode}
            />
          </div>
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
