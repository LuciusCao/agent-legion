import { describe, expectTypeOf, it } from 'vitest'

import { getWorkspaceAgents } from './api'
import type {
  WorkspaceAgentAssignmentTransport,
  WorkspaceAgentDraft,
  WorkspaceAgentListTransport,
  WorkspaceAgentRequestTransport,
} from './api-contract'

describe('workspace agent API contracts', () => {
  it('exposes the complete generated transport shape', () => {
    expectTypeOf<WorkspaceAgentAssignmentTransport>().toEqualTypeOf<{
      agent_id: string
      workspace_id: string
      concurrency_limit: number
    }>()
  })

  it('keeps the editable draft independent of workspace_id', () => {
    expectTypeOf<WorkspaceAgentRequestTransport>().toEqualTypeOf<{
      agent_id: string
      concurrency_limit: number
    }>()
    expectTypeOf<WorkspaceAgentDraft>().toEqualTypeOf<WorkspaceAgentRequestTransport>()
  })

  it('returns the complete generated list transport from the GET boundary', () => {
    expectTypeOf(
      getWorkspaceAgents
    ).returns.resolves.toEqualTypeOf<WorkspaceAgentListTransport>()
  })
})
