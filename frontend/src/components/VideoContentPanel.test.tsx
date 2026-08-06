import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { VideoContentPanel } from './VideoContentPanel'
import { createTestQueryClient } from '../testing/testQueryClient'
import { queryKeys } from '../lib/queryKeys'
import type { JobDetail } from '../types/jobTypes'

const mockFetchJobVideoDetail = vi.fn()

vi.mock('../api/jobVideoApi', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api/jobVideoApi')>()
  return {
    ...mod,
    fetchJobVideoDetail: (...args: unknown[]) =>
      mockFetchJobVideoDetail(...args),
  }
})

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function makeDetail(updatedAt: string): JobDetail {
  return {
    job: { id: 'job1', status: 'completed', updated_at: updatedAt },
    nodes: [],
    runs: [],
    artifacts: [],
  } as unknown as JobDetail
}

const mockVideoDetail = {
  input: {
    title: 'Sample Video',
    external_id: 'VID-001',
    source_url: 'https://example.com/video.mp4',
    source_uuid: 'uuid-1',
    content_type: 'knowledge',
    entity_type: 'video',
    legacy_video_id: 'lv1',
    schema_version: 1,
  },
  artifacts: {
    video_url: 'https://cdn.example.com/video.mp4',
    subtitles: [
      { index: 1, start: 0, end: 5, text: 'Hello world' },
      { index: 2, start: 5, end: 10, text: 'Second line' },
    ],
    chapters: [
      { id: 'c1', start: 0, title: 'Intro' },
      { id: 'c2', start: 10, title: 'Body' },
    ],
    interactions: [
      {
        id: 'n1',
        type: 'example_practice',
        trigger_time: 3,
        instruction: '先试做例题',
      },
    ],
    metadata: { duration: 120, resolution: '1080p' },
    review: null,
    checklist: null,
  },
}

describe('VideoContentPanel', () => {
  beforeEach(() => {
    mockFetchJobVideoDetail.mockReset()
    queryClient = createTestQueryClient()
  })

  it('shows loading state then renders video player and timeline', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    render(<VideoContentPanel jobId="job1" />, { wrapper })

    expect(screen.getByText('加载视频内容中...')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByTestId('video-player-wrap')).toBeInTheDocument()
    })
  })

  it('renders collapsible subtitle and interaction panels', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    render(<VideoContentPanel jobId="job1" />, { wrapper })

    await waitFor(() => {
      expect(screen.getByTestId('video-player-wrap')).toBeInTheDocument()
    })

    expect(screen.getByText('字幕')).toBeInTheDocument()
    expect(screen.getByText('2 条')).toBeInTheDocument()
    expect(screen.getByText('交互节点')).toBeInTheDocument()
    expect(screen.getByText('1 条')).toBeInTheDocument()
  })

  it('expands and collapses the subtitle panel', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    render(<VideoContentPanel jobId="job1" />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText('字幕')).toBeInTheDocument()
    })

    expect(screen.queryByText('Hello world')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('字幕'))

    await waitFor(() => {
      expect(screen.getByText('Hello world')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('字幕'))

    await waitFor(() => {
      expect(screen.queryByText('Hello world')).not.toBeInTheDocument()
    })
  })

  it('renders an error message when the endpoint fails', async () => {
    mockFetchJobVideoDetail.mockRejectedValue(new Error('network error'))

    render(<VideoContentPanel jobId="job1" />, { wrapper })

    await waitFor(() => {
      expect(screen.getByText('network error')).toBeInTheDocument()
    })
  })

  it('refetches when the job updated_at version changes in the shared detail query', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)
    queryClient.setQueryData(queryKeys.jobDetail('job1'), makeDetail('t1'))

    render(<VideoContentPanel jobId="job1" />, { wrapper })

    await waitFor(() => {
      expect(screen.getByTestId('video-player-wrap')).toBeInTheDocument()
    })
    expect(mockFetchJobVideoDetail).toHaveBeenCalledTimes(1)

    act(() => {
      queryClient.setQueryData(queryKeys.jobDetail('job1'), makeDetail('t2'))
    })

    await waitFor(() => {
      expect(mockFetchJobVideoDetail).toHaveBeenCalledTimes(2)
    })
  })

  it('pauses and renders an interaction overlay at the trigger time', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    render(<VideoContentPanel jobId="job1" />, { wrapper })

    await waitFor(() => {
      expect(screen.getByTestId('video-player-wrap')).toBeInTheDocument()
    })

    const videoEl = document.getElementById('player') as HTMLVideoElement

    videoEl.currentTime = 3
    fireEvent.timeUpdate(videoEl)

    expect(screen.getByText('例题试做')).toBeInTheDocument()
    expect(screen.getByText('先试做例题')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '我已完成，继续' }))

    await waitFor(() => {
      expect(screen.queryByText('例题试做')).not.toBeInTheDocument()
    })
  })
})
