import { TextField } from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { useWorkspaceSettingsSnapshot } from '../hooks/useWorkspaceSettingsQuery'

export function LocalNodeLimitSection() {
  const { executionConfiguration, setNodeLimit } = useSettingStore()
  const { workflowDefinition, agentRoutes } = useWorkspaceSettingsSnapshot()

  if (!workflowDefinition) return null

  // P-0.5：非 agent 节点一律进入内置 code 池；并发上限保存时由后端按实例
  // code_capacity 校验。agentRoutes 与 node_limits 都按 workspace 取回，节点
  // 只按 node_key 匹配（#211 M3 退役了 workflow_key 维度）。#1079（#440
  // P3b）：自含 agent 节点没有（或只有冻结的、已不展示的）路由行，agent
  // 判定以显式 node_type 为准，路由行只兜底 node_type 缺失的旧记录。
  const agentRouted = new Set(agentRoutes.map((route) => route.node_key))
  const codeNodes = workflowDefinition.nodes.filter(
    (node) => node.node_type !== 'agent' && !agentRouted.has(node.key)
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
