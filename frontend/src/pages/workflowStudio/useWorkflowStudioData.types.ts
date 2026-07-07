import type { useFetchWorkflowRevisionDetail } from './useFetchWorkflowRevisionDetail'

type LoadState = 'loading' | 'ready' | 'empty' | 'error'

export type UseWorkflowStudioDataResult = {
  loadState: LoadState
  workflow: import('../../types').WorkflowDefinitionRecord | null
  revision: import('../../types').WorkflowRevisionSummary | null
  revisions: import('../../types').WorkflowRevisionSummary[]
  originalYaml: string
  reload: () => Promise<void>
  fetchRevisionDetail: ReturnType<typeof useFetchWorkflowRevisionDetail>
}
