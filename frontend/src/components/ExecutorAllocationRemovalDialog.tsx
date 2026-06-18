import { useSettingStore } from '../stores/settingStore'

const dialogStyle = {
  '--md-dialog-container-color': '#ffffff',
} as React.CSSProperties

export function ExecutorAllocationRemovalDialog() {
  const {
    pendingAllocationRemoval,
    executorCatalog,
    executorConfiguration,
    cancelExecutorRemoval,
    confirmExecutorRemoval,
  } = useSettingStore()

  if (!pendingAllocationRemoval) return null

  const executor = executorCatalog.find(
    (e) => e.id === pendingAllocationRemoval
  )
  const affectedBindings = executorConfiguration.bindings.filter(
    (b) => b.executor_id === pendingAllocationRemoval
  )

  return (
    <md-dialog open onClosed={cancelExecutorRemoval} style={dialogStyle}>
      <div slot="headline">
        移除 {executor?.id ?? pendingAllocationRemoval} 执行器
      </div>
      <div slot="content">
        <p>移除执行器会同时清除以下节点绑定</p>
        <ul>
          {affectedBindings.map((binding) => (
            <li key={`${binding.workflow_key}:${binding.node_key}`}>
              {binding.workflow_key} / {binding.node_key}
            </li>
          ))}
        </ul>
      </div>
      <div slot="actions">
        <md-text-button onClick={cancelExecutorRemoval}>取消</md-text-button>
        <md-filled-button onClick={confirmExecutorRemoval}>
          确认
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
