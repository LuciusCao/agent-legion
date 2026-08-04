import {
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  type SelectChangeEvent,
} from '@mui/material'
import { useSettingStore } from '../stores/settingStore'

export function ExecutorBindingSection() {
  const {
    workflowDefinition,
    executorCatalog,
    executorConfiguration,
    agentRoutes,
    setNodeBinding,
  } = useSettingStore()

  const allocatedIds = new Set(
    executorConfiguration.allocations.map((a) => a.executor_id)
  )

  if (!workflowDefinition) return null

  const workflowKey = workflowDefinition.key

  // Agent nodes are routed by Agent ID (AgentRoutingSection); backend rejects executor bindings for them.
  const agentNodeKeys = new Set(
    agentRoutes
      .filter((route) => route.workflow_key === workflowKey)
      .map((route) => route.node_key)
  )

  const handleChange = (nodeKey: string) => (event: SelectChangeEvent) => {
    const value = event.target.value
    setNodeBinding(workflowKey, nodeKey, value === '' ? null : value)
  }

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
        节点绑定
      </h3>

      <ul
        style={{
          listStyle: 'none',
          margin: 0,
          padding: 0,
          display: 'grid',
          gap: 12,
        }}
      >
        {workflowDefinition.nodes
          .filter((node) => !agentNodeKeys.has(node.key))
          .map((node) => {
            const currentBinding = executorConfiguration.bindings.find(
              (b) => b.workflow_key === workflowKey && b.node_key === node.key
            )
            const compatibleExecutors = executorCatalog.filter(
              (executor) =>
                allocatedIds.has(executor.id) &&
                executor.capabilities.includes(node.capability)
            )
            const hasSupport = compatibleExecutors.length > 0
            const label = `绑定 ${node.key}`

            return (
              <li
                key={node.key}
                style={{
                  display: 'grid',
                  gap: 8,
                  padding: 12,
                  border: '1px solid #c3c6cf',
                  borderRadius: 12,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                  }}
                >
                  <span
                    style={{
                      flex: 1,
                      fontWeight: 500,
                      minWidth: 0,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {node.label}
                  </span>
                  <span
                    style={{
                      fontSize: 12,
                      padding: '2px 8px',
                      borderRadius: 999,
                      background: '#f0f0f0',
                      color: '#43474e',
                      flexShrink: 0,
                    }}
                  >
                    {node.capability}
                  </span>
                </div>

                <FormControl fullWidth>
                  <InputLabel id={`binding-label-${node.key}`}>
                    {label}
                  </InputLabel>
                  <Select
                    labelId={`binding-label-${node.key}`}
                    label={label}
                    aria-label={label}
                    data-testid={`binding-select-${node.key}`}
                    value={currentBinding?.executor_id ?? ''}
                    onChange={handleChange(node.key)}
                  >
                    <MenuItem value="">未绑定</MenuItem>
                    {compatibleExecutors.map((executor) => (
                      <MenuItem key={executor.id} value={executor.id}>
                        {executor.id} ({executor.kind})
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                {!hasSupport && (
                  <div
                    style={{
                      fontSize: 12,
                      color: '#ba1a1a',
                    }}
                  >
                    没有已分配的执行器支持能力 {node.capability}
                  </div>
                )}
              </li>
            )
          })}
      </ul>
    </div>
  )
}
