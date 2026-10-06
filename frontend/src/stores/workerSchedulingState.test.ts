import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { createWorkerStatusActions } from './workerSchedulingState'
import type { WorkerStatusResponse } from '../types'

vi.mock('../api', () => ({ api: vi.fn() }))
const request = vi.mocked(api)

function deferred() {
  let resolve!: (value: WorkerStatusResponse) => void
  let reject!: (error: Error) => void
  const promise = new Promise<WorkerStatusResponse>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('workspace scheduling response ordering', () => {
  it.each(['before', 'during'])(
    'ignores a GET started %s a successful mutation',
    async (when) => {
      const read = deferred()
      const write = deferred()
      const actions = createWorkerStatusActions()
      request.mockImplementation((url) =>
        String(url).includes('/status') ? read.promise : write.promise
      )
      const oldRead =
        when === 'before' ? actions.fetchWorkerStatus('a') : undefined
      const mutation = actions.setWorkerPaused(false, 'a')
      await Promise.resolve()
      const pendingRead = oldRead ?? actions.fetchWorkerStatus('a')
      write.resolve({ paused: false })
      await expect(mutation).resolves.toBe(false)
      read.resolve({ paused: true })
      await expect(pendingRead).resolves.toEqual({
        paused: true,
        superseded: true,
      })
    }
  )

  it('does not let an older GET replace a more recent successful read', async () => {
    const old = deferred()
    const next = deferred()
    const actions = createWorkerStatusActions()
    request.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
    const first = actions.fetchWorkerStatus('a')
    const second = actions.fetchWorkerStatus('a')
    next.resolve({ paused: false })
    await expect(second).resolves.toEqual({ paused: false, superseded: false })
    old.resolve({ paused: true })
    await expect(first).resolves.toEqual({ paused: true, superseded: true })
  })

  it('recovers with a fresh GET and retry after mutation failure', async () => {
    const write = deferred()
    const read = deferred()
    const actions = createWorkerStatusActions()
    request.mockImplementation((url) =>
      String(url).includes('/status') ? read.promise : write.promise
    )
    const mutation = actions.setWorkerPaused(false, 'a')
    const failed = expect(mutation).rejects.toThrow('offline')
    const pendingRead = actions.fetchWorkerStatus('a')
    await Promise.resolve()
    write.reject(new Error('offline'))
    await failed
    read.resolve({ paused: false })
    await expect(pendingRead).resolves.toMatchObject({ superseded: true })
    request.mockResolvedValue({ paused: true })
    await expect(actions.fetchWorkerStatus('a')).resolves.toEqual({
      paused: true,
      superseded: false,
    })
    request.mockResolvedValue({ paused: false })
    await expect(actions.setWorkerPaused(false, 'a')).resolves.toBe(false)
  })

  it('serializes same-workspace writes without blocking other workspaces', async () => {
    const first = deferred()
    const actions = createWorkerStatusActions()
    request.mockImplementation((url) => {
      if (String(url).includes('/pause?workspace_id=a')) return first.promise
      return Promise.resolve({ paused: false })
    })
    const order: string[] = []
    const pause = actions
      .setWorkerPaused(true, 'a')
      .then((value) => order.push(`a:${value}`))
    const resume = actions
      .setWorkerPaused(false, 'a')
      .then((value) => order.push(`a:${value}`))
    await actions
      .setWorkerPaused(false, 'b')
      .then((value) => order.push(`b:${value}`))
    expect(request.mock.calls.map(([url]) => url)).not.toContain(
      '/api/worker/resume?workspace_id=a'
    )
    expect(order).toEqual(['b:false'])
    first.resolve({ paused: true })
    await Promise.all([pause, resume])
    expect(order).toEqual(['b:false', 'a:true', 'a:false'])
    expect(request.mock.calls.map(([url]) => url)).toContain(
      '/api/worker/resume?workspace_id=a'
    )
  })

  it.each([false, true])(
    'preserves successful queued writes when firstFails=%s',
    async (firstFails) => {
      const first = deferred()
      const second = deferred()
      let paused = true
      const actions = createWorkerStatusActions()
      request
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise)
      // 调用方（useWorkerScheduling）把每个成功结果写回缓存：这里用局部
      // 变量模拟缓存写回。
      const commit = (value: boolean) => {
        paused = value
      }
      const resume = actions.setWorkerPaused(false, 'a')
      const pause = actions.setWorkerPaused(true, 'a')
      void resume.then(commit, () => {})
      void pause.then(commit, () => {})
      const failed = expect(firstFails ? resume : pause).rejects.toThrow(
        'offline'
      )
      await Promise.resolve()
      if (firstFails) {
        first.reject(new Error('offline'))
        await failed
        second.resolve({ paused: true })
        await pause
        expect(paused).toBe(true)
      } else {
        first.resolve({ paused: false })
        await resume
        expect(paused).toBe(false)
        second.reject(new Error('offline'))
        await failed
        expect(paused).toBe(false)
      }
    }
  )
})
