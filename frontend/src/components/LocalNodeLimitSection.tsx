import { TextField } from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { useWorkspaceSettingsSnapshot } from '../hooks/useWorkspaceSettingsQuery'

export function LocalNodeLimitSection() {
  const { executorConfiguration, setNodeLimit } = useSettingStore()
  const { workflowDefinition, executorCatalog } = useWorkspaceSettingsSnapshot()

  if (!workflowDefinition) return null

  const workflowKey = workflowDefinition.key
  const allocatedMap = new Map(
    executorConfiguration.allocations.map((a) => [
      a.executor_id,
      a.concurrency_limit,
    ])
  )

  const localBoundNodes = workflowDefinition.nodes.filter((node) => {
    const binding = executorConfiguration.bindings.find(
      (b) => b.workflow_key === workflowKey && b.node_key === node.key
    )
    if (!binding) return false
    const executor = executorCatalog.find((e) => e.id === binding.executor_id)
    return executor?.kind === 'code'
  })

  if (localBoundNodes.length === 0) return null

  return (
    <div>
      <h3
        style={{
          fontSize: 14,
          fontWeight: 500,
          margin: '0 0 12px',
          color: '#43474e',
        }}
      >
        代码节点并发
      </h3>

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        {localBoundNodes.map((node) => {
          const binding = executorConfiguration.bindings.find(
            (b) => b.workflow_key === workflowKey && b.node_key === node.key
          )
          const max = binding ? (allocatedMap.get(binding.executor_id) ?? 1) : 1
          const limit = executorConfiguration.node_limits.find(
            (l) => l.workflow_key === workflowKey && l.node_key === node.key
          )

          return (
            <div
              key={node.key}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
              }}
            >
              <span style={{ fontSize: 14, minWidth: 120 }}>{node.label}</span>
              <TextField
                type="number"
                inputProps={{ min: 1, max }}
                label={`${node.label} 并发上限`}
                value={limit?.concurrency_limit ?? ''}
                onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
                  const raw = event.target.value
                  const value = Number(raw)
                  setNodeLimit(
                    workflowKey,
                    node.key,
                    raw === '' || Number.isNaN(value) ? null : value
                  )
                }}
                size="small"
                sx={{ width: 140 }}
              />
              <span
                style={{
                  fontSize: 12,
                  color: '#43474e',
                }}
              >
                上限: {max}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
