import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { useWorkspaceSettingsSnapshot } from '../hooks/useWorkspaceSettingsQuery'

export function ExecutorAllocationRemovalDialog() {
  const {
    pendingAllocationRemoval,
    executorConfiguration,
    cancelExecutorRemoval,
    confirmExecutorRemoval,
  } = useSettingStore()
  const { executorCatalog } = useWorkspaceSettingsSnapshot()

  if (!pendingAllocationRemoval) return null

  const executor = executorCatalog.find(
    (e) => e.id === pendingAllocationRemoval
  )
  const affectedBindings = executorConfiguration.bindings.filter(
    (b) => b.executor_id === pendingAllocationRemoval
  )

  return (
    <Dialog open onClose={cancelExecutorRemoval}>
      <DialogTitle>
        移除 {executor?.id ?? pendingAllocationRemoval} 执行器
      </DialogTitle>
      <DialogContent>
        <p>移除执行器会同时清除以下节点绑定</p>
        <ul>
          {affectedBindings.map((binding) => (
            <li key={`${binding.workflow_key}:${binding.node_key}`}>
              {binding.workflow_key} / {binding.node_key}
            </li>
          ))}
        </ul>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={cancelExecutorRemoval}>
          取消
        </Button>
        <Button variant="contained" onClick={confirmExecutorRemoval}>
          确认
        </Button>
      </DialogActions>
    </Dialog>
  )
}
