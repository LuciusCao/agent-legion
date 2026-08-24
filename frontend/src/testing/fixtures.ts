import { vi } from 'vitest'
import type { AgentsState } from '../stores/agentsStore'
import type { UiState } from '../stores/uiStore'
import type { JobSummary } from '../types/jobTypes'

export function createMockAgentsState(
  partial: Partial<AgentsState> = {}
): AgentsState {
  return {
    agents: [],
    workerPausedByWorkspace: {},
    getWorkerPaused: vi.fn(() => false),
    connectAgentsWs: vi.fn(() => vi.fn()),
    fetchWorkerStatus: vi.fn(),
    setWorkerPaused: vi.fn(),
    ...partial,
  }
}

export function createMockUiState(partial: Partial<UiState> = {}): UiState {
  return {
    workspacePackageDialogOpen: false,
    tokenUsageDialogOpen: false,
    toast: null,
    pageTitle: null,
    pageSubtitle: null,
    detailPageActions: null,
    setWorkspacePackageDialogOpen: vi.fn(),
    setTokenUsageDialogOpen: vi.fn(),
    showToast: vi.fn(),
    clearToast: vi.fn(),
    setPageTitle: vi.fn(),
    setPageSubtitle: vi.fn(),
    setDetailPageActions: vi.fn(),
    ...partial,
  }
}
export function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: 'j1',
    workspace_id: 'ws1',
    workflow_key: 'p1',
    source_id: 'Q1',
    source_type: 'question',
    title: '',
    status: 'pending',
    batch_id: 'b1',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    storage_dir: '/tmp/j1',
    error_message: '',
    error_summary: '',
    completed_nodes: 0,
    total_nodes: 0,
    workflow_revision_id: '',
    workflow_version: null,
    workflow_definition_hash: '',
    outcome: '',
    current_workflow_revision_id: '',
    current_workflow_revision_version: null,
    is_workflow_outdated: false,
    packed: 0,
    ...overrides,
  }
}
