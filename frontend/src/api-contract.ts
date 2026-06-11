import type { components } from './generated/api'

type Schemas = components['schemas']

export type WorkspaceAgentAssignmentTransport =
  Schemas['WorkspaceAgentAssignmentResponse']
export type WorkspaceAgentListTransport = Schemas['WorkspaceAgentListResponse']
export type WorkspaceAgentRequestTransport = Schemas['WorkspaceAgentConfig']
export type WorkspaceAgentDraft = WorkspaceAgentRequestTransport
