import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useVideoEvents } from './useVideoEvents'
import { useVideoStore } from '../stores/videoStore'
import { EventSourceMock } from '../testing/eventSourceMock'
import * as api from '../api'
import * as download from '../lib/download'

vi.mock('../api')

const mockFetchPackages = vi.mocked(api.fetchPackages)

vi.mock('../lib/download', () => ({
  triggerDownload: vi.fn(),
}))

const mockTriggerDownload = vi.mocked(download.triggerDownload)

describe('useVideoEvents', () => {
  const originalEventSource = globalThis.EventSource

  const storage: Record<string, string> = {}

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
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
        failed: 0,
        completed: 0,
        packed: 0,
        unpacked: 0,
      },
    })
    vi.clearAllMocks()
    mockFetchPackages.mockResolvedValue({ packages: [] })
    mockTriggerDownload.mockClear()
    Object.keys(storage).forEach((k) => delete storage[k])
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage[key] ?? null,
      setItem: (key: string, value: string) => {
        storage[key] = value
      },
      removeItem: (key: string) => {
        delete storage[key]
      },
      clear: () => {
        Object.keys(storage).forEach((k) => delete storage[k])
      },
    })
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  it('connects to global video SSE endpoint', async () => {
    renderHook(() => useVideoEvents())
    await waitFor(() => {
      expect(EventSourceMock.instances.length).toBe(1)
    })
    expect(EventSourceMock.instances[0].url).toBe('/api/videos/events')
  })

  it('marks SSE connected on open', async () => {
    renderHook(() => useVideoEvents())
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onopen?.()
    })

    expect(useVideoStore.getState().sseConnected).toBe(true)
  })

  it('does not connect when disabled', () => {
    renderHook(() => useVideoEvents(false))
    expect(EventSourceMock.instances.length).toBe(0)
  })

  it('does not connect when EventSource is unavailable', () => {
    globalThis.EventSource = undefined as unknown as typeof EventSource
    renderHook(() => useVideoEvents())
    expect(EventSourceMock.instances.length).toBe(0)
  })

  it('merges video_updated payloads', async () => {
    renderHook(() => useVideoEvents())
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'video_updated',
            video: {
              id: 'v1',
              title: 'Updated',
              content_type: 'knowledge',
              status: 'completed',
            },
          }),
        })
      )
    })

    await waitFor(() => {
      expect(useVideoStore.getState().videos).toHaveLength(1)
    })
    expect(useVideoStore.getState().videos[0].title).toBe('Updated')
  })

  it('removes video_deleted payloads', async () => {
    useVideoStore.setState({
      videos: [
        {
          id: 'v1',
          title: 'Video 1',
          content_type: 'knowledge',
          status: 'completed',
        } as unknown as import('../types').VideoItem,
      ],
    })
    renderHook(() => useVideoEvents())
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'video_deleted',
            video_id: 'v1',
          }),
        })
      )
    })

    await waitFor(() => {
      expect(useVideoStore.getState().videos).toHaveLength(0)
    })
  })

  it('triggers download for package_ready payloads', async () => {
    const fetchVideos = vi.spyOn(useVideoStore.getState(), 'fetchVideos')
    fetchVideos.mockResolvedValue(undefined)

    renderHook(() => useVideoEvents())
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'package_ready',
            download_url: '/api/packages/pkg.zip',
          }),
        })
      )
    })

    await waitFor(() => {
      expect(mockTriggerDownload).toHaveBeenCalledWith('/api/packages/pkg.zip')
    })
    expect(fetchVideos).toHaveBeenCalled()
  })

  it('ignores heartbeat and invalid payloads', async () => {
    renderHook(() => useVideoEvents())
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onmessage?.(
        new MessageEvent('message', {
          data: ':heartbeat',
        })
      )
      source.onmessage?.(
        new MessageEvent('message', {
          data: 'not-json',
        })
      )
    })

    await act(async () => new Promise((resolve) => setTimeout(resolve, 50)))
    expect(useVideoStore.getState().videos).toHaveLength(0)
  })

  it('reconnects after an error', async () => {
    vi.useFakeTimers()
    renderHook(() => useVideoEvents())
    const source = EventSourceMock.instances[0]

    await act(async () => {
      source.onerror?.()
    })

    await act(async () => {
      vi.advanceTimersByTime(1100)
    })

    expect(EventSourceMock.instances.length).toBe(2)
    vi.useRealTimers()
  })

  it('downloads pending package on mount when newer than last downloaded', async () => {
    mockFetchPackages.mockResolvedValue({
      packages: [
        {
          id: 42,
          name: 'pkg.zip',
          path: '/packages/pkg.zip',
          video_count: 1,
          size_bytes: 100,
          locked: 0,
          created_at: '2026-01-01',
        },
      ],
    })

    renderHook(() => useVideoEvents())

    await waitFor(() => {
      expect(mockTriggerDownload).toHaveBeenCalledWith('/api/packages/pkg.zip')
    })
    expect(localStorage.getItem('video-hive:last-downloaded-package-id')).toBe(
      '42'
    )
  })

  it('skips download when package was already downloaded', async () => {
    localStorage.setItem('video-hive:last-downloaded-package-id', '42')
    mockFetchPackages.mockResolvedValue({
      packages: [
        {
          id: 42,
          name: 'pkg.zip',
          path: '/packages/pkg.zip',
          video_count: 1,
          size_bytes: 100,
          locked: 0,
          created_at: '2026-01-01',
        },
      ],
    })

    renderHook(() => useVideoEvents())

    await act(async () => new Promise((resolve) => setTimeout(resolve, 50)))
    expect(mockTriggerDownload).not.toHaveBeenCalled()
  })

  it('ignores empty package list', async () => {
    mockFetchPackages.mockResolvedValue({ packages: [] })

    renderHook(() => useVideoEvents())

    await act(async () => new Promise((resolve) => setTimeout(resolve, 50)))
    expect(mockTriggerDownload).not.toHaveBeenCalled()
  })

  it('closes EventSource on cleanup', () => {
    const { unmount } = renderHook(() => useVideoEvents())
    const source = EventSourceMock.instances[0]

    unmount()

    expect(source.close).toHaveBeenCalled()
  })
})
