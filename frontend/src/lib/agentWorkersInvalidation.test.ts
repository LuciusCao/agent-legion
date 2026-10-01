import { afterEach, expect, it, vi } from 'vitest'
import { createTestQueryClient } from '../testing/testQueryClient'
import { invalidateAgentWorkers } from './agentWorkersInvalidation'
import { queryKeys } from './queryKeys'
import { extraQueryKeys } from './queryKeysExtra'

afterEach(() => vi.useRealTimers())

it('coalesces updates and invalidates both global and scoped lists without refetching deployment metadata', async () => {
  vi.useFakeTimers()
  const client = createTestQueryClient()
  client.setDefaultOptions({ queries: { gcTime: Infinity } })
  const lists = [
    queryKeys.agentWorkers(),
    extraQueryKeys.workspaceWorkers('a'),
    extraQueryKeys.workspaceWorkers('b'),
  ]
  for (const key of lists) client.setQueryData(key, [])
  client.setQueryData(extraQueryKeys.workerConsole(), {
    console_url: 'http://localhost:8787',
  })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  invalidateAgentWorkers(client)
  await vi.advanceTimersByTimeAsync(500)
  invalidateAgentWorkers(client)
  await vi.advanceTimersByTimeAsync(749)
  expect(invalidate).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)
  expect(invalidate).toHaveBeenCalledTimes(2)
  for (const key of lists)
    expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  expect(
    client.getQueryState(extraQueryKeys.workerConsole())?.isInvalidated
  ).toBe(false)
  client.clear()
})
