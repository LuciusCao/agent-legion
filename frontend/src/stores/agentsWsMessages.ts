import type { AgentStatus } from '../types'

interface AgentsSnapshotEnvelope {
  type: 'snapshot'
  agents: AgentStatus[]
}

interface AgentUpdateEnvelope {
  type: 'agent_busy' | 'agent_idle'
  agent: AgentStatus
}

type AgentsWsMessage = AgentStatus[] | AgentUpdateEnvelope

/**
 * Parse one /api/agents WS message. Envelope protocol (phase 4, task 9):
 * `{type: 'snapshot', agents}` replaces the full list; `agent_busy` /
 * `agent_idle` carry a single updated agent. A bare array from older
 * servers is treated as a full snapshot (window compatibility).
 */
export function parseAgentsWsMessage(data: string): AgentsWsMessage | null {
  const parsed = JSON.parse(data) as
    | AgentStatus[]
    | AgentsSnapshotEnvelope
    | AgentUpdateEnvelope
  if (Array.isArray(parsed)) return parsed
  if (parsed.type === 'snapshot') return parsed.agents
  if (parsed.type === 'agent_busy' || parsed.type === 'agent_idle') {
    return parsed
  }
  return null
}

/** Replace the agent with the same id + workspace, or append a new one. */
export function upsertAgent(
  agents: AgentStatus[],
  incoming: AgentStatus
): AgentStatus[] {
  const index = agents.findIndex(
    (agent) =>
      agent.id === incoming.id && agent.workspace_id === incoming.workspace_id
  )
  if (index === -1) return [...agents, incoming]
  const next = agents.slice()
  next[index] = incoming
  return next
}
