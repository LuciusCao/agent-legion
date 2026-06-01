import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { BatchToolbar } from './BatchToolbar'
import { useUiStore } from '../stores/uiStore'
import { useVideoStore } from '../stores/videoStore'

const mockApi = vi.fn()
vi.mock('../api', () => ({
  api: (...args: any[]) => mockApi(...args),
}))

describe('BatchToolbar', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useUiStore.setState({
      agents: [],
      addDialogOpen: false,
      addContentType: 'knowledge',
      rerunDialogOpen: false,
      deleteDialogOpen: false,
      toast: null,
    })
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
          status: 'completed',
          current_phase: 'package',
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

  it('opens delete dialog and shows an error toast when batch delete has failed items', async () => {
    mockApi
      .mockResolvedValueOnce({
        results: [
          { video_id: 'v1', status: 'deleted', message: '' },
          { video_id: 'v2', status: 'not_found', message: 'Video not found' },
        ],
      })
      .mockResolvedValueOnce({ videos: [] })

    render(<BatchToolbar />)

    await act(async () => {
      screen.getByTitle('删除').click()
    })

    expect(screen.getByText('确认删除')).toBeInTheDocument()

    await act(async () => {
      screen.getByText('删除').click()
    })

    await waitFor(() => {
      expect(useUiStore.getState().toast).toEqual({
        message: '删除完成：成功 1 项，失败 1 项',
        type: 'error',
      })
    })
  })

  it('opens rerun dialog when rerun button is clicked', async () => {
    render(<BatchToolbar />)

    expect(screen.getByText('未打包')).toBeInTheDocument()
    expect(screen.getByText('仅已通过')).toBeInTheDocument()
    expect(screen.getByText('未通过/部分通过')).toBeInTheDocument()

    await act(async () => {
      screen.getByTitle('重跑').click()
    })

    expect(screen.getByText('选择重跑阶段')).toBeInTheDocument()
  })

  it('submits selected videos for packaging', async () => {
    mockApi.mockResolvedValueOnce({ accepted: true })

    render(<BatchToolbar />)

    await act(async () => {
      screen.getByTitle('打包').click()
    })

    expect(mockApi).toHaveBeenCalledWith(
      '/api/package',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ video_ids: ['v1', 'v2'] }),
      })
    )
    expect(useUiStore.getState().toast).toEqual({
      message: '打包已提交，完成后将自动下载',
      type: 'success',
    })
    expect(useVideoStore.getState().selectMode).toBe(false)
  })

  it('opens run-to dialog and submits selected videos', async () => {
    mockApi
      .mockResolvedValueOnce({ results: [] })
      .mockResolvedValueOnce({ videos: [] })

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
          status: 'queued',
          current_phase: 'subtitle_review',
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
          status: 'queued',
          current_phase: 'subtitle_review',
          error_message: '',
        },
      ],
    })

    const { container } = render(<BatchToolbar />)

    await act(async () => {
      screen.getByTitle('运行到').click()
    })
    expect(screen.getByText('运行到阶段')).toBeInTheDocument()

    const assembleChip = container.querySelector(
      'md-filter-chip[label="组装"]'
    ) as HTMLElement
    expect(assembleChip).toBeInTheDocument()
    await act(async () => {
      assembleChip.click()
    })

    await act(async () => {
      screen.getByText('运行到组装').click()
    })

    expect(mockApi).toHaveBeenCalledWith(
      '/api/videos/batch/run-to',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          video_ids: ['v1', 'v2'],
          target_phase: 'assemble',
          start_phase: null,
        }),
      })
    )
  })
})
