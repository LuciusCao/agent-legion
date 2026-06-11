import { vi } from 'vitest'
import type { UiState } from '../stores/uiStore'
import type { JobRecord, VideoItem } from '../types'

export function createMockUiState(partial: Partial<UiState> = {}): UiState {
  return {
    agents: [],
    addDialogOpen: false,
    addContentType: 'knowledge',
    addDialogContext: 'video',
    addDialogWorkspaceId: undefined,
    rerunDialogOpen: false,
    deleteDialogOpen: false,
    workerPaused: false,
    toast: null,
    pageTitle: null,
    detailPageActions: null,
    connectAgentsWs: vi.fn(() => vi.fn()),
    fetchWorkerStatus: vi.fn(),
    setWorkerPaused: vi.fn(),
    openAddDialog: vi.fn(),
    closeAddDialog: vi.fn(),
    setAddContentType: vi.fn(),
    openRerunDialog: vi.fn(),
    closeRerunDialog: vi.fn(),
    openDeleteDialog: vi.fn(),
    closeDeleteDialog: vi.fn(),
    showToast: vi.fn(),
    clearToast: vi.fn(),
    setPageTitle: vi.fn(),
    setDetailPageActions: vi.fn(),
    ...partial,
  }
}

export function makeVideo(overrides: Partial<VideoItem> = {}): VideoItem {
  return {
    id: 'v1',
    title: 'Test Video',
    source_url: '',
    content_type: 'knowledge',
    external_id: '',
    knowledge_code: '',
    question_id: '',
    source_uuid: '',
    status: 'queued',
    current_phase: 'download',
    error_message: '',
    storage_dir: '',
    duration: 0,
    packed: false,
    ...overrides,
  }
}

export function makeJob(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    id: 'j1',
    workspace_id: 'ws1',
    pipeline_key: 'p1',
    source_id: 'Q1',
    title: '',
    stem: '',
    status: 'pending',
    ...overrides,
  }
}
