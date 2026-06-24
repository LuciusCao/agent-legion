import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { usePhaseRunsTimeline } from './usePhaseRunsTimeline'
import { api } from '../api'
import type { TranscriptionRun } from '../types'

vi.mock('../api', () => ({
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

const baseRun = {
  id: 1,
  video_id: 'v1',
  phase_key: 'download',
  status: 'completed',
  started_at: new Date(Date.now() - 10000).toISOString(),
  finished_at: new Date(Date.now() - 5000).toISOString(),
  command_json: JSON.stringify(['pi', '--agent', 'review', '--agent=foo']),
  exit_code: 0,
  log_path: '',
  error_message: '',
}

const runningRun = {
  ...baseRun,
  id: 2,
  phase_key: 'transcribe',
  status: 'running',
  finished_at: null,
}

function transRun(
  partial: Partial<TranscriptionRun> &
    Pick<TranscriptionRun, 'id' | 'provider' | 'started_at'>
): TranscriptionRun {
  return {
    video_id: 'v1',
    status: 'completed',
    finished_at: null,
    srt_entry_count: 0,
    validation_summary: '',
    fallback_reason: '',
    ...partial,
  }
}

describe('usePhaseRunsTimeline', () => {
  beforeEach(() => {
    mockApi.mockReset()
  })

  it('returns latest items for knowledge content', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline([baseRun], [], 'knowledge', 'download', 'completed')
    )

    expect(result.current.items).toHaveLength(1)
    expect(result.current.items[0].run.phase_key).toBe('download')
  })

  it('returns latest items for question content', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline(
        [{ ...baseRun, phase_key: 'subtitle_review' }],
        [],
        'question',
        'subtitle_review',
        'completed'
      )
    )

    expect(result.current.items).toHaveLength(1)
    expect(result.current.items[0].run.phase_key).toBe('subtitle_review')
  })

  it('synthesizes a running item for the current phase', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline(
        [{ ...baseRun, status: 'completed' }],
        [],
        'knowledge',
        'download',
        'running'
      )
    )

    const item = result.current.items.find(
      (i) => i.run.phase_key === 'download'
    )
    expect(item?.run.status).toBe('running')
    expect(item?.run.id).toBeLessThan(0)
  })

  it('builds history items with occurrence counts', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline(
        [baseRun, { ...baseRun, id: 3, phase_key: 'download' }],
        [],
        'knowledge',
        'download',
        'completed'
      )
    )

    act(() => result.current.setViewMode('history'))
    expect(result.current.items).toHaveLength(2)
    expect(result.current.items[0].occurrence).toBe(1)
    expect(result.current.items[1].occurrence).toBe(2)
  })

  it('formats transcription provider names', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline(
        [runningRun],
        [
          transRun({
            id: 1,
            provider: 'whisper',
            started_at: new Date().toISOString(),
          }),
        ],
        'knowledge',
        'transcribe',
        'running'
      )
    )

    const item = result.current.items.find(
      (i) => i.run.phase_key === 'transcribe'
    )
    expect(item?.tool).toBe('whisper.cpp')
  })

  it('extracts openclaw agent and argument values', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline([baseRun], [], 'knowledge', 'download', 'completed')
    )

    expect(
      result.current.extractOpenClawArg(baseRun.command_json, '--agent')
    ).toBe('review')
    expect(result.current.extractOpenClawArg('["--x=1"]', '--x')).toBe('1')
    expect(result.current.extractOpenClawArg('invalid', '--x')).toBe('')
    expect(result.current.extractOpenClawArg('["--x"]', '--x')).toBe('')
    expect(result.current.extractOpenClawArg('["--y=1"]', '--x')).toBe('')
  })

  it('extracts agent name from --agent= form', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline(
        [{ ...baseRun, command_json: JSON.stringify(['--agent=foo']) }],
        [],
        'knowledge',
        'download',
        'completed'
      )
    )

    expect(result.current.items[0].tool).toBe('openclaw-foo')
  })

  it('returns empty tool for invalid command json', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline(
        [{ ...baseRun, command_json: 'not-json' }],
        [],
        'knowledge',
        'download',
        'completed'
      )
    )

    expect(result.current.items[0].tool).toBe('')
  })

  it('formats transcription provider for empty, whisper, sensevoice and other values', () => {
    const now = new Date()
    const { result } = renderHook(() =>
      usePhaseRunsTimeline(
        [runningRun],
        [
          transRun({
            id: 1,
            provider: 'custom',
            started_at: now.toISOString(),
          }),
          transRun({
            id: 2,
            provider: '',
            started_at: new Date(now.getTime() - 2000).toISOString(),
          }),
          transRun({
            id: 3,
            provider: 'whisper',
            started_at: new Date(now.getTime() - 1000).toISOString(),
          }),
        ],
        'knowledge',
        'transcribe',
        'running'
      )
    )

    const item = result.current.items.find(
      (i) => i.run.phase_key === 'transcribe'
    )
    expect(item?.tool).toBe('custom')
  })

  it('toggles expanded details', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline([baseRun], [], 'knowledge', 'download', 'completed')
    )

    act(() => result.current.toggleDetail(1))
    expect(result.current.expandedDetails.has(1)).toBe(true)

    act(() => result.current.toggleDetail(1))
    expect(result.current.expandedDetails.has(1)).toBe(false)
  })

  it('opens a session and loads its log', async () => {
    mockApi.mockResolvedValueOnce({ log: 'session log' })

    const { result } = renderHook(() =>
      usePhaseRunsTimeline([baseRun], [], 'knowledge', 'download', 'completed')
    )

    await act(async () => {
      await result.current.openSession(baseRun, 'sess1')
    })

    expect(result.current.sessionDialog).toEqual({
      runId: 1,
      sessionId: 'sess1',
      videoId: 'v1',
    })
    expect(result.current.sessionLogs[1]).toBe('session log')
  })

  it('shows a fallback message when session log fetch fails', async () => {
    mockApi.mockRejectedValueOnce(new Error('not found'))

    const { result } = renderHook(() =>
      usePhaseRunsTimeline([baseRun], [], 'knowledge', 'download', 'completed')
    )

    await act(async () => {
      await result.current.openSession(baseRun, 'sess1')
    })

    expect(result.current.sessionLogs[1]).toBe('会话文件暂不可用')
  })

  it('does not reload a session log that is already loaded', async () => {
    mockApi.mockResolvedValueOnce({ log: 'session log' })

    const { result } = renderHook(() =>
      usePhaseRunsTimeline([baseRun], [], 'knowledge', 'download', 'completed')
    )

    await act(async () => {
      await result.current.openSession(baseRun, 'sess1')
    })

    mockApi.mockClear()
    await act(async () => {
      await result.current.openSession(baseRun, 'sess1')
    })

    expect(mockApi).not.toHaveBeenCalled()
  })

  it('identifies primary and fallback transcription runs', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline(
        [],
        [
          transRun({
            id: 1,
            provider: 'whisper',
            started_at: new Date(Date.now() - 1000).toISOString(),
          }),
          transRun({
            id: 2,
            provider: 'sensevoice',
            status: 'fallback',
            started_at: new Date().toISOString(),
            fallback_reason: 'primary failed',
          }),
        ],
        'knowledge',
        'transcribe',
        'completed'
      )
    )

    expect(result.current.transPrimary?.provider).toBe('sensevoice')
    expect(result.current.transFallback?.fallback_reason).toBe('primary failed')
  })

  it('uses the non-fallback transcription run as the tool', () => {
    const { result } = renderHook(() =>
      usePhaseRunsTimeline(
        [runningRun],
        [
          transRun({
            id: 1,
            provider: 'whisper',
            status: 'fallback',
            started_at: new Date().toISOString(),
            fallback_reason: 'primary failed',
          }),
          transRun({
            id: 2,
            provider: 'sensevoice',
            started_at: new Date(Date.now() - 1000).toISOString(),
          }),
        ],
        'knowledge',
        'transcribe',
        'running'
      )
    )

    const item = result.current.items.find(
      (i) => i.run.phase_key === 'transcribe'
    )
    expect(item?.tool).toBe('SenseVoice')
  })

  it('pauses and resumes the now timer on visibility change', async () => {
    const addEventListenerSpy = vi.spyOn(document, 'addEventListener')
    const removeEventListenerSpy = vi.spyOn(document, 'removeEventListener')

    const { unmount } = renderHook(() =>
      usePhaseRunsTimeline([baseRun], [], 'knowledge', 'download', 'completed')
    )

    expect(addEventListenerSpy).toHaveBeenCalledWith(
      'visibilitychange',
      expect.any(Function)
    )

    const handler = addEventListenerSpy.mock.calls.find(
      (call) => call[0] === 'visibilitychange'
    )?.[1] as EventListener

    Object.defineProperty(document, 'hidden', {
      value: true,
      configurable: true,
      writable: true,
    })
    act(() => {
      handler(new Event('visibilitychange'))
    })

    Object.defineProperty(document, 'hidden', {
      value: false,
      configurable: true,
      writable: true,
    })
    act(() => {
      handler(new Event('visibilitychange'))
    })

    unmount()
    expect(removeEventListenerSpy).toHaveBeenCalledWith(
      'visibilitychange',
      handler
    )
  })
})
