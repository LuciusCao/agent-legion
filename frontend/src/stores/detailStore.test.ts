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
})
