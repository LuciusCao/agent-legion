import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { VideoContentPanel } from './VideoContentPanel'

const mockFetchJobVideoDetail = vi.fn()

vi.mock('../videoApi', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../videoApi')>()
  return {
    ...mod,
    fetchJobVideoDetail: (...args: unknown[]) =>
      mockFetchJobVideoDetail(...args),
  }
})

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
  })

  it('shows loading state then renders video title and external id', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    render(<VideoContentPanel jobId="job1" />)

    expect(screen.getByText('加载视频内容中...')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('Sample Video')).toBeInTheDocument()
    })
    expect(screen.getByText(/VID-001/)).toBeInTheDocument()
  })

  it('renders subtitle, chapter and interaction summaries', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    render(<VideoContentPanel jobId="job1" />)

    await waitFor(() => {
      expect(screen.getByText('Sample Video')).toBeInTheDocument()
    })

    expect(screen.getByText('2 条')).toBeInTheDocument()
    expect(screen.getByText('1 个')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '字幕' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '交互节点' })).toBeInTheDocument()
  })

  it('renders an error message when the endpoint fails', async () => {
    mockFetchJobVideoDetail.mockRejectedValue(new Error('network error'))

    render(<VideoContentPanel jobId="job1" />)

    await waitFor(() => {
      expect(screen.getByText('network error')).toBeInTheDocument()
    })
  })

  it('refetches when refreshKey changes', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    const { rerender } = render(
      <VideoContentPanel jobId="job1" refreshKey="t1" />
    )

    await waitFor(() => {
      expect(screen.getByText('Sample Video')).toBeInTheDocument()
    })
    expect(mockFetchJobVideoDetail).toHaveBeenCalledTimes(1)

    rerender(<VideoContentPanel jobId="job1" refreshKey="t2" />)

    await waitFor(() => {
      expect(mockFetchJobVideoDetail).toHaveBeenCalledTimes(2)
    })
  })
})
