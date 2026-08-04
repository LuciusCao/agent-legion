import { Link } from 'react-router-dom'
import { useSettingStore } from '../stores/settingStore'
import { AgentCapacityInput } from './AgentCapacityInput'

const chipStyle = {
  fontSize: 12,
  padding: '2px 8px',
  borderRadius: 999,
  background: '#f0f0f0',
  color: '#43474e',
  flexShrink: 0,
} as const

const headingStyle = {
  fontSize: 14,
  fontWeight: 500,
  margin: '0 0 12px',
  color: '#43474e',
} as const

/**
 * Workspace-level Agent capacity setting plus a read-only view of the
 * workspace's Agent routes.
 *
 * The concurrency cap is workspace-level: it bounds the total in-flight Agent
 * node executions across all Workers for this workspace, and is saved through
 * the page's saveAll flow (workspace configuration PUT). Agent nodes are
 * bound to Agent Definitions by the published workflow revision — never to
 * physical Workers, which the broker picks dynamically at claim time.
 * Registered Workers are listed in the WorkerTokensSection below.
 */
export function AgentRoutingSection() {
  const { agentRoutes, settings, workspaceId } = useSettingStore()

  const routes = agentRoutes.filter(
    (route) =>
      !settings.workflowKey || route.workflow_key === settings.workflowKey
  )

  return (
    <div>
      <h3 style={headingStyle}>Agent 并发上限</h3>
      <AgentCapacityInput />

      <h3 style={headingStyle}>Agent 节点</h3>

      {routes.length === 0 ? (
        <p style={{ fontSize: 13, color: '#74777f', margin: '0 0 12px' }}>
          当前 workflow 没有 Agent 节点
        </p>
      ) : (
        <ul
          style={{
            listStyle: 'none',
            margin: '0 0 12px',
            padding: 0,
            display: 'grid',
            gap: 12,
          }}
        >
          {routes.map((route) => (
            <li
              key={`${route.workflow_key}-${route.node_key}`}
              data-testid={`agent-route-${route.node_key}`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: 12,
                border: '1px solid #c3c6cf',
                borderRadius: 12,
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
                {route.node_label}
              </span>
              <span style={chipStyle}>{route.capability}</span>
              <span style={chipStyle} title={route.agent_skill}>
                {route.agent_id}
              </span>
            </li>
          ))}
        </ul>
      )}

      <p style={{ fontSize: 12, color: '#74777f', margin: '0 0 20px' }}>
        并发上限为 workspace 级，见上方设置；Agent 由 workflow
        定义决定，调整请前往{' '}
        <Link to={`/workspaces/${workspaceId}/workflow-studio`}>
          Workflow Studio
        </Link>{' '}
        编辑并重新发布。Worker 由 Broker 在执行时动态分配，无需绑定。
      </p>
    </div>
  )
}
