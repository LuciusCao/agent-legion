import { TextField } from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { useWorkspaceSettingsSnapshot } from '../hooks/useWorkspaceSettingsQuery'
import { codeNodeKeys } from '../lib/codeNodes'

export function LocalNodeLimitSection() {
  const { executionConfiguration, setNodeLimit } = useSettingStore()
  const { workflowDefinition, agentRoutes } = useWorkspaceSettingsSnapshot()

  if (!workflowDefinition) return null

  // P-0.5：非 agent 节点一律进入内置 code 池；并发上限保存时由后端按实例
  // code_capacity 校验。按显式类型判定（#933：自含 agent 节点无 Agent 路由，
  // 不能按「无路由」当成 code 节点）；agentRoutes 只兜底缺 node_type 的旧 payload。
  const codeKeys = codeNodeKeys(workflowDefinition.nodes, agentRoutes)
  const codeNodes = workflowDefinition.nodes.filter((node) =>
    codeKeys.has(node.key)
  )

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
