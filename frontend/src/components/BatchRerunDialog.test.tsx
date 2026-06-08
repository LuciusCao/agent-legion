import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { BatchRerunDialog } from './BatchRerunDialog'
import { useVideoStore } from '../stores/videoStore'

const mockApi = vi.fn()
vi.mock('../api', () => ({
  api: (...args: any[]) => mockApi(...args),
}))

describe('BatchRerunDialog', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useVideoStore.setState({
      videos: [
        {
          id: 'v1',
          title: 'Video 1',
          source_url: '',
          content_type: 'knowledge',
          external_id: 'K001',
          knowledge_code: 'K001',
          question_id: '',
          source_uuid: '',
          status: 'completed',
          current_phase: 'package',
          error_message: '',
        },
        {
          id: 'v2',
          title: 'Video 2',
          source_url: '',
          content_type: 'knowledge',
          external_id: 'K002',
          knowledge_code: 'K002',
          question_id: '',
          source_uuid: '',
          status: 'failed',
          current_phase: 'subtitle_review',
          error_message: '',
        },
      ],
      selectedType: 'knowledge',
      statusFilter: 'all',
      searchQuery: '',
      selectMode: true,
      selectedIds: new Set(['v1', 'v2']),
      isLoading: false,
    })
  })

  it('renders chips and video list', () => {
    const { container } = render(
      <BatchRerunDialog open videoIds={['v1', 'v2']} onClose={() => {}} />
    )

    expect(screen.getByText('选择重跑阶段')).toBeInTheDocument()
    expect(
      container.querySelector('md-filter-chip[label="下载"]')
    ).toBeInTheDocument()
    expect(
      container.querySelector('md-filter-chip[label="转录"]')
    ).toBeInTheDocument()
    expect(screen.getByText('K001')).toBeInTheDocument()
    expect(screen.getByText('K002')).toBeInTheDocument()
  })

  it('marks non-rerunnable videos when selecting a later phase', () => {
    const { container } = render(
      <BatchRerunDialog open videoIds={['v1', 'v2']} onClose={() => {}} />
    )

    // By default "download" is selected, both videos can rerun
    expect(screen.queryByText(/无法重跑/)).not.toBeInTheDocument()

    // Click "assemble" chip — v2 at subtitle_review cannot rerun from assemble
    const assembleChip = container.querySelector('md-filter-chip[label="组装"]')
    expect(assembleChip).toBeInTheDocument()
    act(() => {
      ;(assembleChip as HTMLElement).click()
    })

    expect(screen.getByText(/当前处于 字幕审核/)).toBeInTheDocument()
  })

  it('calls batchRerun on confirm', async () => {
    mockApi
      .mockResolvedValueOnce({ results: [] })
      .mockResolvedValueOnce({ videos: [] })

    const onClose = vi.fn()
    render(<BatchRerunDialog open videoIds={['v1', 'v2']} onClose={onClose} />)

    await act(async () => {
      screen.getByText('重跑 2 个视频').click()
    })

    expect(mockApi).toHaveBeenCalledWith(
      '/api/videos/batch/rerun',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ video_ids: ['v1', 'v2'], phase: 'download' }),
      })
    )
    expect(onClose).toHaveBeenCalled()
    expect(useVideoStore.getState().selectedIds.size).toBe(0)
    expect(useVideoStore.getState().selectMode).toBe(false)
  })

  it('renders failed-phase chip and filters runnable videos', () => {
    const { container } = render(
      <BatchRerunDialog open videoIds={['v1', 'v2']} onClose={() => {}} />
    )

    // Click "失败的阶段" chip
    const failedChip = container.querySelector(
      'md-filter-chip[label="失败的阶段"]'
    )
    expect(failedChip).toBeInTheDocument()
    act(() => {
      ;(failedChip as HTMLElement).click()
    })

    // v1 is completed, v2 is failed — only v2 should be runnable
    expect(screen.getByText('未失败，跳过')).toBeInTheDocument()
    expect(screen.getByText('重跑 1 个视频')).toBeInTheDocument()
  })

  it('calls batchRerun with __failed__ phase when failed-phase chip selected', async () => {
    mockApi
      .mockResolvedValueOnce({ results: [] })
      .mockResolvedValueOnce({ videos: [] })

    const onClose = vi.fn()
    const { container } = render(
      <BatchRerunDialog open videoIds={['v1', 'v2']} onClose={onClose} />
    )

    const failedChip = container.querySelector(
      'md-filter-chip[label="失败的阶段"]'
    )
    act(() => {
      ;(failedChip as HTMLElement).click()
    })

    await act(async () => {
      screen.getByText('重跑 1 个视频').click()
    })

    expect(mockApi).toHaveBeenCalledWith(
      '/api/videos/batch/rerun',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ video_ids: ['v1', 'v2'], phase: '__failed__' }),
      })
    )
  })
})
