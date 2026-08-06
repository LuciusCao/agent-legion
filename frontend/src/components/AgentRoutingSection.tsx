import { AgentCapacityInput } from './AgentCapacityInput'

const headingStyle = {
  fontSize: 14,
  fontWeight: 500,
  margin: '0 0 12px',
  color: '#43474e',
} as const

/**
 * Workspace-level Agent capacity setting: it bounds the total in-flight Agent
 * node executions across all Workers for this workspace, and is saved through
 * the page's saveAll flow (workspace configuration PUT). Agent routing itself
 * is defined by the published workflow revision (visualized in Workflow
 * Studio); Workers are managed in the WorkerTokensSection below.
 */
export function AgentRoutingSection() {
  return (
    <div>
      <h3 style={headingStyle}>Agent 并发上限</h3>
      <AgentCapacityInput />
    </div>
  )
}
