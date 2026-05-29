import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useDetailStore } from './detailStore'

vi.mock('../api', () => ({
  api: vi.fn(),
}))

import { api } from '../api'
const mockApi = vi.mocked(api)

describe('detailStore', () => {
  beforeEach(() => {
    useDetailStore.setState({
      currentVideo: null,
      log: '',
      activeTab: 'nodes',
      isLoading: false,
      error: null,
    })
    mockApi.mockClear()
  })

  it('loads video and sets active tab', async () => {
    mockApi.mockResolvedValueOnce({
      video: {
        id: 'v1',
        title: 'Test',
        content_type: 'question',
        status: 'queued',
      },
    })
    await useDetailStore.getState().loadVideo('v1')
    expect(useDetailStore.getState().currentVideo?.id).toBe('v1')
    expect(useDetailStore.getState().activeTab).toBe('subtitles')
  })

  it('sets active tab to subtitles for knowledge videos', async () => {
    mockApi.mockResolvedValueOnce({
      video: {
        id: 'v2',
        title: 'Test K',
        content_type: 'knowledge',
        status: 'queued',
      },
    })
    await useDetailStore.getState().loadVideo('v2')
    expect(useDetailStore.getState().currentVideo?.id).toBe('v2')
    expect(useDetailStore.getState().activeTab).toBe('subtitles')
  })

  it('sets error when loadVideo fails', async () => {
    mockApi.mockRejectedValueOnce(new Error('network error'))
    await useDetailStore.getState().loadVideo('v3')
    expect(useDetailStore.getState().currentVideo).toBeNull()
    expect(useDetailStore.getState().activeTab).toBe('nodes')
    expect(useDetailStore.getState().error).toBe('network error')
    expect(useDetailStore.getState().isLoading).toBe(false)
  })

  it('sets error and fallback log when loadLog fails', async () => {
    mockApi.mockRejectedValueOnce(new Error('log error'))
    await useDetailStore.getState().loadLog('v4')
    expect(useDetailStore.getState().log).toBe('加载日志失败')
    expect(useDetailStore.getState().error).toBe('log error')
  })

  it('sets error when loadPhaseRuns fails', async () => {
    mockApi.mockRejectedValueOnce(new Error('phase error'))
    await useDetailStore.getState().loadPhaseRuns('v5')
    expect(useDetailStore.getState().error).toBe('phase error')
  })
})
