import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useVideoPhaseEvents } from './useVideoPhaseEvents'
import { useDetailStore } from '../stores/detailStore'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  url: string

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  close = vi.fn()
}

describe('useVideoPhaseEvents', () => {
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    FakeEventSource.instances = []
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource
    useDetailStore.setState({
      currentVideo: null,
      phaseRuns: [],
      transcriptionRuns: [],
      log: '',
      activeTab: 'nodes',
      isLoading: false,
    })
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  it('updates the detail video and phase runs from per-video SSE payloads', () => {
    renderHook(() => useVideoPhaseEvents('v1'))
    const source = FakeEventSource.instances[0]

    act(() => {
      source.onmessage?.({
        data: JSON.stringify({
          type: 'phase_runs_updated',
          video: {
            id: 'v1',
            title: 'Video 1',
            source_url: '',
            content_type: 'knowledge',
            external_id: 'K001',
            knowledge_code: 'K001',
            question_id: '',
            status: 'running',
            current_phase: 'transcribe',
            error_message: '',
          },
          phase_runs: [
            {
              id: 1,
              video_id: 'v1',
              phase_key: 'transcribe',
              status: 'running',
            },
          ],
          transcription_runs: [],
        }),
      } as MessageEvent)
    })

    expect(useDetailStore.getState().currentVideo?.current_phase).toBe(
      'transcribe'
    )
    expect(useDetailStore.getState().phaseRuns).toHaveLength(1)
  })
})
