import { TextField } from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { useWorkspaceSettingsSnapshot } from '../hooks/useWorkspaceSettingsQuery'
import { codePoolNodes } from './codePoolNodes'

export function LocalNodeLimitSection() {
  const { executionConfiguration, setNodeLimit } = useSettingStore()
  const { workflowDefinition, agentRoutes } = useWorkspaceSettingsSnapshot()

  if (!workflowDefinition) return null

  // 并发上限保存时由后端按实例 code_capacity 校验；判定口径见 codePoolNodes。
  const codeNodes = codePoolNodes(workflowDefinition.nodes, agentRoutes)

  if (codeNodes.length === 0) return null

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
        {codeNodes.map((node) => {
          const limit = executionConfiguration.node_limits.find(
            (l) => l.node_key === node.key
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
                inputProps={{ min: 1 }}
                label={`${node.label} 并发上限`}
                value={limit?.concurrency_limit ?? ''}
                onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
                  const raw = event.target.value
                  const value = Number(raw)
                  setNodeLimit(
                    node.key,
                    raw === '' || Number.isNaN(value) ? null : value
                  )
                }}
                size="small"
                sx={{ width: 140 }}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}
