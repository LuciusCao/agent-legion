import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { DetailPage } from './DetailPage'
import { useDetailStore } from '../stores/detailStore'
import { useArtifactStore } from '../stores/artifactStore'
import { useInteractionStore } from '../stores/interactionStore'
import { useVideoStore } from '../stores/videoStore'
import { useUiStore } from '../stores/uiStore'

vi.mock('../api', () => ({
  api: vi.fn(),
}))

import { api } from '../api'

const mockApi = vi.mocked(api)

describe('DetailPage', () => {
  beforeEach(() => {
    global.ResizeObserver = vi.fn().mockImplementation(function () {
      return {
        observe: vi.fn(),
        disconnect: vi.fn(),
        unobserve: vi.fn(),
      }
    })
    useDetailStore.setState({
      currentVideo: null,
      log: '',
      activeTab: 'subtitles',
      isLoading: false,
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
      selectMode: false,
      selectedIds: new Set(),
      isLoading: false,
    })
    useUiStore.setState({
      agents: [],
      addDialogOpen: false,
      addContentType: 'knowledge',
      rerunDialogOpen: false,
      deleteDialogOpen: false,
      toast: null,
    })
    mockApi.mockReset()
  })

  it('lets the top bar span above the full-height phase panel', async () => {
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: 'v1',
          title: 'Video 1',
          source_url: 'https://example.com/v1.mp4',
          content_type: 'knowledge',
          external_id: 'K001',
          knowledge_code: 'K001',
          question_id: '',
          status: 'completed',
          current_phase: 'assemble',
          error_message: '',
          storage_dir: '/tmp/v1',
          duration: 120,
          packed: true,
        },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: 'ok' })

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/videos/v1']}
      >
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Video 1')).toBeInTheDocument()
    })

    const upper = document.querySelector('.detail-upper')
    const primary = document.querySelector('.detail-primary')
    const topbar = document.querySelector('.detail-topbar')
    const titleBlock = document.querySelector('.detail-title-block')
    const sidebar = document.querySelector('.phase-runs-sidebar') as HTMLElement

    expect(upper).toContainElement(primary)
    expect(upper).toContainElement(sidebar)
    expect(upper).toContainElement(topbar)
    expect(titleBlock).toContainElement(screen.getByText('已完成'))
    expect(titleBlock).toContainElement(screen.getByText('已打包'))
    expect(topbar?.querySelector('.phase-name')).not.toBeInTheDocument()
    expect(topbar?.querySelector('.detail-progress')).not.toBeInTheDocument()
    expect(primary).not.toContainElement(topbar)
    expect(upper?.firstElementChild).toBe(topbar)
    expect(topbar?.nextElementSibling).toBe(primary)
    expect(primary?.nextElementSibling).toBe(sidebar)
    expect(sidebar.style.getPropertyValue('--detail-primary-height')).toBe('')
    expect(sidebar.style.height).toBe('')
  })

  it('renders chapter and interaction chip rows below the player', async () => {
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: 'v1',
          title: 'Video 1',
          source_url: 'https://example.com/v1.mp4',
          content_type: 'knowledge',
          external_id: 'K001',
          knowledge_code: 'K001',
          question_id: '',
          status: 'completed',
          current_phase: 'assemble',
          error_message: '',
          storage_dir: '/tmp/v1',
          duration: 120,
        },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [{ index: 1, start: 1, end: 3, text: '字幕内容' }],
        chapters: [{ id: 'c1', start: 12, end: 30, title: '第一章' }],
        interactions: [
          {
            trigger_time: 5,
            instruction: '节点内容',
            type: 'example_practice',
          },
        ],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: 'ok' })

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/videos/v1']}
      >
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Video 1')).toBeInTheDocument()
    })

    expect(screen.getByText('章节')).toBeInTheDocument()
    expect(screen.getByText('互动')).toBeInTheDocument()

    const chips = Array.from(document.querySelectorAll('md-suggestion-chip'))
    expect(
      chips.some((chip) => chip.getAttribute('label') === '0:12 第一章')
    ).toBe(true)
    expect(
      chips.some((chip) => chip.getAttribute('label') === '0:05 例题试做')
    ).toBe(true)
    expect(
      chips.some((chip) => chip.getAttribute('label')?.includes('节点内容'))
    ).toBe(false)
    expect(
      document.querySelector("[data-testid='timeline-track']")
    ).not.toBeInTheDocument()
  })

  it('keeps the more menu after delete and replays a node from interaction details', async () => {
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: 'v1',
          title: 'Video 1',
          source_url: 'https://example.com/v1.mp4',
          content_type: 'knowledge',
          external_id: 'K001',
          knowledge_code: 'K001',
          question_id: '',
          status: 'completed',
          current_phase: 'assemble',
          error_message: '',
          storage_dir: '/tmp/v1',
          duration: 120,
        },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [
          {
            id: 'n1',
            trigger_time: 5,
            instruction: '节点内容',
            answer: ['hello'],
          },
        ],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: 'ok' })

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/videos/v1']}
      >
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Video 1')).toBeInTheDocument()
    })

    const actions = document.querySelector('.detail-actions')
    const deleteButton = screen.getByTitle('删除')
    const moreButton = screen.getByTitle('更多')
    expect(actions?.lastElementChild).toBe(moreButton)
    expect(deleteButton.nextElementSibling).toBe(moreButton)

    fireEvent.click(moreButton)
    fireEvent.click(screen.getByText('交互节点'))

    const nodeEntry = await screen.findByText('节点内容')
    fireEvent.click(nodeEntry)

    expect(await screen.findByText('hello')).toBeInTheDocument()
  })

  it('exposes the full detail title through a custom hover tooltip', async () => {
    const longTitle =
      'x09050501 这是一个特别长的知识点名称，用来确认详情页标题悬停时显示全称'
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: 'v1',
          title: longTitle,
          source_url: 'https://example.com/v1.mp4',
          content_type: 'knowledge',
          external_id: 'x09050501',
          knowledge_code: 'x09050501',
          question_id: '',
          status: 'completed',
          current_phase: 'assemble',
          error_message: '',
          storage_dir: '/tmp/v1',
          duration: 120,
        },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: 'ok' })

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/videos/v1']}
      >
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      const heading = screen.getByRole('heading', { name: longTitle })
      expect(heading).not.toHaveAttribute('title')
      expect(heading.parentElement).toHaveAttribute('data-tooltip', longTitle)
    })
  })

  it('pauses and shows an interaction when playback crosses a trigger time', async () => {
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: 'v1',
          title: 'Video 1',
          source_url: 'https://example.com/v1.mp4',
          content_type: 'knowledge',
          external_id: 'K001',
          knowledge_code: 'K001',
          question_id: '',
          status: 'completed',
          current_phase: 'assemble',
          error_message: '',
          storage_dir: '/tmp/v1',
          duration: 120,
        },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [
          {
            id: 'n1',
            trigger_time: '0:05',
            type: 'example_practice',
            instruction: '暂停做题',
          },
        ],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: 'ok' })

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/videos/v1']}
      >
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(document.querySelector('video')).toBeInTheDocument()
    })
    const video = document.querySelector('video') as HTMLVideoElement
    Object.defineProperty(video, 'currentTime', {
      value: 4.8,
      configurable: true,
    })
    act(() => {
      video.dispatchEvent(new Event('play', { bubbles: true }))
    })
    act(() => {
      video.dispatchEvent(new Event('timeupdate', { bubbles: true }))
    })

    Object.defineProperty(video, 'currentTime', {
      value: 6.7,
      configurable: true,
    })
    act(() => {
      video.dispatchEvent(new Event('timeupdate', { bubbles: true }))
    })

    expect(screen.getByText('暂停做题')).toBeInTheDocument()
  })

  it('keeps delete dialog open and shows an error when deleting fails', async () => {
    mockApi
      .mockResolvedValueOnce({
        video: {
          id: 'v1',
          title: 'Video 1',
          source_url: 'https://example.com/v1.mp4',
          content_type: 'knowledge',
          external_id: 'K001',
          knowledge_code: 'K001',
          question_id: '',
          status: 'completed',
          current_phase: 'assemble',
          error_message: '',
          storage_dir: '/tmp/v1',
          duration: 120,
        },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: 'ok' })
      .mockRejectedValueOnce(new Error('delete failed'))

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/videos/v1']}
      >
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Video 1')).toBeInTheDocument()
    })

    act(() => {
      useUiStore.getState().openDeleteDialog()
    })

    await act(async () => {
      screen.getByText('删除').click()
    })

    await waitFor(() => {
      expect(useUiStore.getState().deleteDialogOpen).toBe(true)
      expect(useUiStore.getState().toast).toEqual({
        message: '删除失败: delete failed',
        type: 'error',
      })
    })
  })

  it('submits a single-video run-to request', async () => {
    mockApi
      .mockResolvedValueOnce({
        video: {
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
          storage_dir: '',
          duration: 0,
          packed: false,
        },
        phase_runs: [],
        transcription_runs: [],
      })
      .mockResolvedValueOnce({
        subtitles: [],
        chapters: [],
        interactions: [],
        metadata: null,
        review: null,
        checklist: null,
      })
      .mockResolvedValueOnce({ log: '' })
      .mockResolvedValueOnce({
        result: {
          video_id: 'v1',
          status: 'run_to',
          phase: 'chapter_generate',
          message: '',
        },
        video: null,
      })
      .mockResolvedValueOnce({ videos: [] })

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/videos/v1']}
      >
        <Routes>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>
    )

    const runToButton = await screen.findByTitle('运行到')
    await act(async () => {
      runToButton.click()
    })
    await act(async () => {
      screen.getByText('运行到章节生成').click()
    })

    expect(mockApi).toHaveBeenCalledWith(
      '/api/videos/v1/run-to',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          target_phase: 'chapter_generate',
          start_phase: null,
        }),
      })
    )
  })
})
