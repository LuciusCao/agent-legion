import { describe, expectTypeOf, it } from 'vitest'

import type {
  WorkspaceAgentAssignmentTransport,
  WorkspaceAgentDraft,
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
    expectTypeOf<WorkspaceAgentDraft>().toEqualTypeOf<{
      agent_id: string
      concurrency_limit: number
    }>()
  })
})
