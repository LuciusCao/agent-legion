import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioLayout } from './WorkflowStudioLayout'

const workflow = {
  key: 'video_knowledge',
  label: '知识视频 DAG',
  intake: { modes: [] },
  nodes: [],
  edges: [],
}

const revision = {
  id: 'rev-active',
  workspace_id: 'ws1',
  workflow_key: 'video_knowledge',
  version: 1,
  status: 'active',
  definition_hash: '17d8077e',
  created_at: '2026-07-06T10:00:00Z',
  published_at: '2026-07-06T10:05:00Z',
}

const baseProps = {
  loadState: 'ready' as const,
  actionState: 'idle' as const,
  workflow,
  revision,
  activeRevision: revision,
  revisions: [revision],
  definitionYaml: 'key: video_knowledge\nlabel: 知识视频 DAG\n',
  setDefinitionYaml: vi.fn(),
  selectedNodeKey: null,
  setSelectedNodeKey: vi.fn(),
  validationErrors: [],
  validationMessage: '',
  compareErrors: null,
  compareSummary: null,
  compareState: 'idle' as const,
  dirty: false,
  canSubmit: false,
  canPublish: false,
  nodes: [],
  edges: [],
  reviewDialogOpen: false,
  closeReviewDialog: vi.fn(),
  dagFullscreenOpen: false,
  setDagFullscreenOpen: vi.fn(),
  onValidate: vi.fn(),
  onPublish: vi.fn(),
  onReset: vi.fn(),
  publishDraft: vi.fn(),
  viewMode: 'draft' as const,
  selectedRevisionId: revision.id,
  readOnly: false,
  hasPreservedDraft: false,
  isLoadingRevision: false,
  revisionLoadError: null,
  selectRevision: vi.fn(),
  backToDraft: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

describe('WorkflowStudioLayout', () => {
  it('renders mobile panel navigation landmarks', () => {
    render(<WorkflowStudioLayout {...baseProps} />)

    const mobileNav = screen.getByRole('tablist', {
      name: 'Workflow studio panels',
    })
    expect(mobileNav).toBeInTheDocument()
    expect(
      within(mobileNav).getByRole('tab', { name: 'Versions' })
    ).toBeInTheDocument()
    expect(
      within(mobileNav).getByRole('tab', { name: 'Graph' })
    ).toBeInTheDocument()
    expect(
      within(mobileNav).getByRole('tab', { name: 'Inspector' })
    ).toBeInTheDocument()
    expect(
      within(mobileNav).getByRole('tab', { name: 'Changes' })
    ).toBeInTheDocument()
    expect(
      within(mobileNav).getByRole('tab', { name: 'YAML' })
    ).toBeInTheDocument()
  })
})
