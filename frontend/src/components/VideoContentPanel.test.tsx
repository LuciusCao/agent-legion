import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
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

  it('shows loading state then renders video player and timeline', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    render(<VideoContentPanel jobId="job1" />)

    expect(screen.getByText('加载视频内容中...')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByTestId('video-player-wrap')).toBeInTheDocument()
    })
  })

  it('renders collapsible subtitle and interaction panels', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    render(<VideoContentPanel jobId="job1" />)

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

    render(<VideoContentPanel jobId="job1" />)

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
      expect(screen.getByTestId('video-player-wrap')).toBeInTheDocument()
    })
    expect(mockFetchJobVideoDetail).toHaveBeenCalledTimes(1)

    rerender(<VideoContentPanel jobId="job1" refreshKey="t2" />)

    await waitFor(() => {
      expect(mockFetchJobVideoDetail).toHaveBeenCalledTimes(2)
    })
  })

  it('pauses and renders an interaction overlay at the trigger time', async () => {
    mockFetchJobVideoDetail.mockResolvedValue(mockVideoDetail)

    render(<VideoContentPanel jobId="job1" />)

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
