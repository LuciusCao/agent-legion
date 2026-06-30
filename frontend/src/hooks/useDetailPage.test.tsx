import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useDetailPage } from './useDetailPage'
import { useDetailStore } from '../stores/detailStore'
import { useArtifactStore } from '../stores/artifactStore'
import { useInteractionStore } from '../stores/interactionStore'
import { useVideoStore } from '../stores/videoStore'
import { useUiStore } from '../stores/uiStore'
import { api } from '../api'

vi.mock('../api', () => ({
  api: vi.fn(),
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useParams: () => ({ id: 'v1' }),
    useNavigate: () => mockNavigate,
  }
})

const mockApi = vi.mocked(api)

const baseVideo = {
  id: 'v1',
  title: 'Video 1',
  source_url: 'https://example.com/v1.mp4',
  content_type: 'knowledge' as const,
  external_id: 'K001',
  knowledge_code: 'K001',
  question_id: '',
  status: 'completed',
  current_phase: 'assemble',
  error_message: '',
  storage_dir: '/tmp/v1',
  duration: 120,
  packed: true,
  source_uuid: '',
}

const baseArtifacts = {
  subtitles: [],
  chapters: [],
  interactions: [
    {
      id: 'n1',
      trigger_time: '0:05',
      type: 'example_practice' as const,
      instruction: '暂停做题',
    },
  ],
  metadata: null,
  review: null,
  checklist: null,
}

function mockDetailLoad({
  video = baseVideo,
  artifacts = baseArtifacts,
  log = '',
}: {
  video?: typeof baseVideo | null
  artifacts?: typeof baseArtifacts
  log?: string
} = {}) {
  mockApi
    .mockResolvedValueOnce({
      video,
      phase_runs: [],
      transcription_runs: [],
    })
    .mockResolvedValueOnce(artifacts)
    .mockResolvedValueOnce({ log })
}

function resetStores() {
  mockApi.mockReset()
  mockNavigate.mockReset()
  useDetailStore.setState({
    currentVideo: null,
    log: '',
    phaseRuns: [],
    transcriptionRuns: [],
    activeTab: 'subtitles',
    isLoading: false,
    error: null,
    _loadSeq: 0,
  })
  useArtifactStore.setState({
    artifacts: {
      subtitles: [],
      chapters: [],
      interactions: [],
      metadata: null,
      review: null,
      checklist: null,
    },
  })
  useInteractionStore.setState({
    triggeredNodeIndexes: new Set(),
    dismissedNodeIndexes: new Set(),
    currentSentence: [],
  })
  useVideoStore.setState({
    videos: [],
    selectedType: 'knowledge',
    statusFilter: 'all',
    searchQuery: '',
    packedFilter: 'all',
    selectMode: false,
    selectedIds: new Set(),
    isLoading: false,
    sseConnected: true,
    error: null,
    _filteredVideos: [],
    _counts: {
      all: 0,
      queued: 0,
      running: 0,
      completed: 0,
      packed: 0,
      unpacked: 0,
    },
  })
  useUiStore.setState({
    agents: [],
    addDialogOpen: false,
    addContentType: 'knowledge',
    addDialogContext: 'video',
    addDialogWorkspaceId: undefined,
    rerunDialogOpen: false,
    deleteDialogOpen: false,
    workspacePackageDialogOpen: false,
    workerPaused: true,
    workerPausedByWorkspace: {},
    toast: null,
  })
}

describe('useDetailPage', () => {
  beforeEach(resetStores)

  it('loads video, artifacts and log on mount', async () => {
    mockDetailLoad({ log: 'ok' })

    const { result } = renderHook(() => useDetailPage())

    await waitFor(() => expect(result.current.video).not.toBeNull())
    expect(result.current.video?.title).toBe('Video 1')
    expect(result.current.artifacts.interactions).toHaveLength(1)
    expect(result.current.detailTitle).toBe('Video 1')
  })

  it('reloads artifacts and log when phase/status changes', async () => {
    mockApi
      .mockResolvedValueOnce({
        video: { ...baseVideo, status: 'running', current_phase: 'download' },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce(baseArtifacts)
      .mockResolvedValueOnce({ log: 'initial' })
      .mockResolvedValueOnce(baseArtifacts)
      .mockResolvedValueOnce({ log: 'updated' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video?.status).toBe('running'))

    act(() => {
      useDetailStore
        .getState()
        .updatePhaseRuns([], [], { ...baseVideo, status: 'completed' })
    })

    await waitFor(() => expect(mockApi).toHaveBeenCalledTimes(5))
  })

  it('shows fallback title when video is missing', async () => {
    mockDetailLoad({ video: null, artifacts: baseArtifacts, log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.detailTitle).toBe('未选择资源'))
  })

  it('handles delete confirm successfully', async () => {
    mockDetailLoad({ log: '' })
    mockApi.mockResolvedValueOnce({}).mockResolvedValueOnce({ videos: [] })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    let deleted = false
    await act(async () => {
      deleted = await result.current.handleDeleteConfirm()
    })

    expect(deleted).toBe(true)
    expect(mockApi).toHaveBeenCalledWith('/api/videos/v1', { method: 'DELETE' })
    expect(useUiStore.getState().toast?.message).toBe('删除成功')
    expect(mockNavigate).toHaveBeenCalledWith('/')
  })

  it('returns false and shows error when delete fails', async () => {
    mockDetailLoad({ log: '' })
    mockApi.mockRejectedValueOnce(new Error('network error'))

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    let deleted = true
    await act(async () => {
      deleted = await result.current.handleDeleteConfirm()
    })

    expect(deleted).toBe(false)
    expect(useUiStore.getState().toast).toEqual({
      message: '删除失败: network error',
      type: 'error',
    })
  })

  it('submits package request and shows success toast', async () => {
    mockDetailLoad({ log: '' })
    mockApi.mockResolvedValueOnce({ accepted: true })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    await act(async () => {
      await result.current.handlePackage()
    })

    expect(mockApi).toHaveBeenCalledWith(
      '/api/package',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ video_ids: ['v1'] }),
      })
    )
    expect(useUiStore.getState().toast?.message).toContain('打包已提交')
  })

  it('submits rerun request and refreshes data', async () => {
    mockDetailLoad({ log: '' })
    mockApi
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ videos: [] })
      .mockResolvedValueOnce({
        video: baseVideo,
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({ log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    await act(async () => {
      await result.current.handleRerun('chapter_generate')
    })

    expect(mockApi).toHaveBeenCalledWith(
      '/api/videos/v1/rerun',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ phase: 'chapter_generate' }),
      })
    )
    expect(useUiStore.getState().toast?.message).toBe('重跑已提交')
  })

  it('shows busy toast when rerun fails with currently processed', async () => {
    mockDetailLoad({ log: '' })
    mockApi.mockRejectedValueOnce(new Error('currently being processed'))

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    await act(async () => {
      await result.current.handleRerun('subtitle_review')
    })

    expect(useUiStore.getState().toast?.message).toContain('正在被处理中')
  })

  it('submits run-to request without start phase', async () => {
    mockDetailLoad({ log: '' })
    mockApi
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ videos: [] })
      .mockResolvedValueOnce({
        video: baseVideo,
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({ log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    await act(async () => {
      await result.current.handleRunTo({
        targetPhase: 'assemble',
        startPhase: null,
      })
    })

    expect(mockApi).toHaveBeenCalledWith(
      '/api/videos/v1/run-to',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ target_phase: 'assemble', start_phase: null }),
      })
    )
    expect(result.current.runToDialogOpen).toBe(false)
  })

  it('submits run-to request with start phase', async () => {
    mockDetailLoad({ log: '' })
    mockApi
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ videos: [] })
      .mockResolvedValueOnce({
        video: baseVideo,
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({ log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    await act(async () => {
      await result.current.handleRunTo({
        targetPhase: 'chapter_generate',
        startPhase: 'subtitle_review',
      })
    })

    expect(useUiStore.getState().toast?.message).toBe('重跑运行已提交')
  })

  it('shows error toast when run-to fails', async () => {
    mockDetailLoad({ log: '' })
    mockApi.mockRejectedValueOnce(new Error('run-to failed'))

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    await act(async () => {
      await result.current.handleRunTo({
        targetPhase: 'assemble',
        startPhase: null,
      })
    })

    expect(useUiStore.getState().toast).toEqual({
      message: '运行失败: run-to failed',
      type: 'error',
    })
  })

  it('triggers interaction when playback crosses trigger time', async () => {
    mockDetailLoad({ log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() =>
      expect(result.current.artifacts.interactions).toHaveLength(1)
    )

    act(() => {
      result.current.setIsPlaying(true)
    })

    act(() => {
      result.current.handleTimeUpdate(6)
    })

    expect(result.current.activeNode?.instruction).toBe('暂停做题')
    expect(result.current.triggeredNodeIndexes.has(0)).toBe(true)
  })

  it('does not trigger already triggered or dismissed interactions', async () => {
    mockDetailLoad({ log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() =>
      expect(result.current.artifacts.interactions).toHaveLength(1)
    )

    act(() => {
      result.current.setIsPlaying(true)
    })
    act(() => {
      result.current.handleTimeUpdate(6)
    })
    expect(result.current.triggeredNodeIndexes.has(0)).toBe(true)

    act(() => {
      result.current.handleTimeUpdate(6.1)
    })
    expect(result.current.triggeredNodeIndexes.has(0)).toBe(true)

    act(() => {
      result.current.handleContinue()
    })
    expect(result.current.dismissedNodeIndexes.has(0)).toBe(true)

    act(() => {
      result.current.handleTimeUpdate(6.2)
    })
    expect(result.current.triggeredNodeIndexes.has(0)).toBe(false)
  })

  it('replays an interaction by resetting triggered and dismissed state', async () => {
    mockDetailLoad({ log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() =>
      expect(result.current.artifacts.interactions).toHaveLength(1)
    )

    act(() => {
      result.current.setIsPlaying(true)
      result.current.handleTimeUpdate(6)
      result.current.pushWord('hello')
    })

    act(() => {
      result.current.replayInteraction(0)
    })

    expect(result.current.triggeredNodeIndexes.has(0)).toBe(true)
    expect(result.current.dismissedNodeIndexes.has(0)).toBe(false)
    expect(result.current.currentSentence).toEqual([])
  })

  it('seeks the player when handleSeek is called', async () => {
    mockDetailLoad({ log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    const video = document.createElement('video')
    Object.defineProperty(result.current.playerRef, 'current', {
      value: video,
      configurable: true,
    })

    act(() => {
      result.current.handleSeek(42)
    })

    expect(video.currentTime).toBe(42)
  })

  it('manages more dialog open/type state', async () => {
    mockDetailLoad({ log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    act(() => {
      result.current.setMoreDialogOpen(true)
    })
    expect(result.current.moreDialogOpen).toBe(true)

    act(() => {
      result.current.openMoreDialog('metadata')
    })
    expect(result.current.moreDialogOpen).toBe(false)
    expect(result.current.moreDialogType).toBe('metadata')

    act(() => {
      result.current.closeMoreDialog()
    })
    expect(result.current.moreDialogType).toBeNull()
  })

  it('opens rerun and delete dialogs via ui store', async () => {
    mockDetailLoad({ log: '' })

    const { result } = renderHook(() => useDetailPage())
    await waitFor(() => expect(result.current.video).not.toBeNull())

    act(() => {
      result.current.openRerunDialog()
    })
    expect(useUiStore.getState().rerunDialogOpen).toBe(true)

    act(() => {
      result.current.openDeleteDialog()
    })
    expect(useUiStore.getState().deleteDialogOpen).toBe(true)
  })
})
