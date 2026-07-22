import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useExecutorsStore, type WorkerSummary } from './executorsStore'

const originalFetch = global.fetch

const workerA: WorkerSummary = {
  worker_id: 'worker-abc123def456',
  name: 'GPU Box A',
  runtimes: ['pi'],
  max_concurrency: 2,
  labels: { gpu: 'true' },
  protocol_version: 1,
  registered_at: '2026-07-20T00:00:00Z',
  last_seen_at: '2026-07-20T01:00:00Z',
  online: true,
  revoked: false,
  allowed_workspaces: [],
}

function mockWorkersFetch(workers: WorkerSummary[]) {
  return vi.fn().mockImplementation((url: string) => {
    if (url === '/api/agent-workers') {
      return Promise.resolve({
        ok: true,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: () => Promise.resolve({ workers }),
        text: () => Promise.resolve(JSON.stringify({ workers })),
      } as Response)
    }
    return Promise.reject(new Error(`unexpected url ${url}`))
  })
}

describe('executorsStore', () => {
  beforeEach(() => {
    useExecutorsStore.setState({ workers: [], connectionStatus: {} })
  })

  afterEach(() => {
    global.fetch = originalFetch
    vi.useRealTimers()
  })

  it('refreshWorkers fetches workers and stores them', async () => {
    vi.useFakeTimers()
    global.fetch = mockWorkersFetch([workerA])

    const promise = useExecutorsStore.getState().refreshWorkers()
    expect(global.fetch).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(750)
    await promise

    expect(global.fetch).toHaveBeenCalledTimes(1)
    expect(useExecutorsStore.getState().workers).toEqual([workerA])
  })

  it('coalesces repeated refreshWorkers calls within 750ms into one request', async () => {
    vi.useFakeTimers()
    global.fetch = mockWorkersFetch([workerA])

    void useExecutorsStore.getState().refreshWorkers()
    await vi.advanceTimersByTimeAsync(400)
    const promise = useExecutorsStore.getState().refreshWorkers()
    await vi.advanceTimersByTimeAsync(400)
    expect(global.fetch).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(400)
    await promise
    expect(global.fetch).toHaveBeenCalledTimes(1)
    expect(useExecutorsStore.getState().workers).toEqual([workerA])
  })

  it('keeps previous workers when the fetch fails', async () => {
    vi.useFakeTimers()
    useExecutorsStore.setState({ workers: [workerA] })
    global.fetch = vi
      .fn()
      .mockRejectedValue(new Error('network down')) as unknown as typeof fetch

    const promise = useExecutorsStore.getState().refreshWorkers()
    await vi.advanceTimersByTimeAsync(750)
    await promise

    expect(useExecutorsStore.getState().workers).toEqual([workerA])
  })

  it('resolves the superseded promise when a newer refreshWorkers call wins', async () => {
    vi.useFakeTimers()
    global.fetch = mockWorkersFetch([workerA])

    const first = useExecutorsStore.getState().refreshWorkers()
    await vi.advanceTimersByTimeAsync(400)
    const second = useExecutorsStore.getState().refreshWorkers()

    // The superseded promise settles immediately instead of hanging forever.
    await first
    await vi.advanceTimersByTimeAsync(750)
    await second

    expect(global.fetch).toHaveBeenCalledTimes(1)
    expect(useExecutorsStore.getState().workers).toEqual([workerA])
  })

  it('setConnectionStatus tracks status per channel', () => {
    useExecutorsStore.getState().setConnectionStatus('agents', 'open')
    expect(useExecutorsStore.getState().connectionStatus.agents).toBe('open')

    useExecutorsStore.getState().setConnectionStatus('agents', 'closed')
    useExecutorsStore.getState().setConnectionStatus('workspace', 'connecting')
    expect(useExecutorsStore.getState().connectionStatus).toEqual({
      agents: 'closed',
      workspace: 'connecting',
    })
  })
})
