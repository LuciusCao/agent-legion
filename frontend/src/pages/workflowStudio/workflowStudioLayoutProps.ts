import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import type { DagGraphEdge, DagGraphNode } from '../../components/DagGraph'
import type { components } from '../../generated/api'
import type { WorkflowStudioRevisionProps } from './workflowStudioRevisionProps'

export type StudioLayoutProps = WorkflowStudioRevisionProps & {
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
  compareErrors: components['schemas']['WorkflowDraftCompareError'][] | null
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
