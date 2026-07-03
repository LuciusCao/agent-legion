import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import type { DagGraphEdge, DagGraphNode } from '../../components/DagGraph'

export type StudioLayoutProps = {
  loadState: 'loading' | 'ready' | 'empty' | 'error'
  actionState: 'idle' | 'validating' | 'publishing'
  workflow: WorkflowDefinitionRecord | null
  revision: WorkflowRevisionSummary | null
  revisions: WorkflowRevisionSummary[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  selectedNodeKey: string | null
  setSelectedNodeKey: (key: string | null) => void
  validationErrors: string[]
  validationMessage: string
  compareErrors:
    | import('../../generated/api').components['schemas']['WorkflowDraftCompareError'][]
    | null
  compareSummary: ChangeSummaryViewModel | null
  compareState: 'idle' | 'loading' | 'ready' | 'error'
  dirty: boolean
  canSubmit: boolean
  canPublish: boolean
  nodes: DagGraphNode[]
  edges: DagGraphEdge[]
  reviewDialogOpen: boolean
  closeReviewDialog: () => void
  dagFullscreenOpen: boolean
  setDagFullscreenOpen: (open: boolean) => void
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
  publishDraft: () => Promise<void>
}
