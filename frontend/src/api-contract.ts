import type { components } from './generated/api'

type Schemas = components['schemas']

export type WorkspaceAgentAssignmentTransport =
  Schemas['WorkspaceAgentAssignmentResponse']
export type WorkspaceAgentListTransport = Schemas['WorkspaceAgentListResponse']
export type WorkspaceAgentDraft = Pick<
  WorkspaceAgentAssignmentTransport,
  'agent_id' | 'concurrency_limit'
>
