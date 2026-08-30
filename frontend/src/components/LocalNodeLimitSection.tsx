import { TextField } from '@mui/material'
import { useSettingStore } from '../stores/settingStore'
import { useWorkspaceSettingsSnapshot } from '../hooks/useWorkspaceSettingsQuery'

export function LocalNodeLimitSection() {
  const { executionConfiguration, setNodeLimit } = useSettingStore()
  const workspaceId = useSettingStore((s) => s.workspaceId)
  const { workflowDefinition, agentRoutes } = useWorkspaceSettingsSnapshot()

  if (!workflowDefinition) return null

  const workflowKey = workflowDefinition.key
  // P-0.5：无 Agent 路由的节点一律进入内置 code 池；并发上限保存时由后端
  // 按实例 code_capacity 校验。agentRoutes 过滤键用 workspace_id
  //（workflow_key 已 deprecated 且 v61 起恒等于 workspace id，#211）；
  // workflowKey 仍用于 node_limits 过滤与 PUT 载荷（请求侧 Phase 2 后续批次）。
  const agentRouted = new Set(
    agentRoutes
      .filter((route) => route.workflow_key === workspaceId)
      .map((route) => route.node_key)
  )
  const codeNodes = workflowDefinition.nodes.filter(
    (node) => !agentRouted.has(node.key)
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
                inputProps={{ min: 1 }}
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
            </div>
          )
        })}
      </div>
    </div>
  )
}
