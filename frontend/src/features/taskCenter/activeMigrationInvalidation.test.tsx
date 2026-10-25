import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { listRunBatches } from '../../api/runBatchesApi'
import { listJobBatchOperations } from '../../api/jobBatchOperationsListApi'
import {
  runBatchKeys,
  useActiveRunBatches,
  useRunBatchWaterfall,
} from './useRunBatches'
import {
  useActiveJobBatchOps,
  useJobBatchOpWaterfall,
} from './useJobBatchOpListing'
import { jobBatchOpKeys } from './useJobBatchOps'

vi.mock('../../api/runBatchesApi', () => ({
  listRunBatches: vi.fn(),
  archiveRunBatch: vi.fn(),
  pauseRunBatch: vi.fn(),
  resumeRunBatch: vi.fn(),
  cancelRunBatch: vi.fn(),
}))

vi.mock('../../api/jobBatchOperationsListApi', () => ({
  listJobBatchOperations: vi.fn(),
  archiveJobBatchOperation: vi.fn(),
}))

const mockRunList = vi.mocked(listRunBatches)
const mockOpList = vi.mocked(listJobBatchOperations)

const runPage = (ids: string[], activeCount: number) => ({
  batches: ids.map((run_id) => ({ run_id })),
  next_cursor: null,
  active_count: activeCount,
})
const opPage = (ids: string[], activeCount: number) => ({
  operations: ids.map((id) => ({ id })),
  next_cursor: null,
  active_count: activeCount,
})

describe('active 迁移驱动同源历史视图失效（#1392）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('覆盖等长替换与计数下降，active 不额外重取且双源不串扰', async () => {
    let runActive = runPage(['run-old'], 1)
    let opActive = opPage(['op-old'], 1)
    const calls = {
      runActive: 0,
      runEnded: 0,
      runArchived: 0,
      opActive: 0,
      opEnded: 0,
      opArchived: 0,
    }
    mockRunList.mockImplementation((_workspace, options) => {
      if (options.view === 'active') {
        calls.runActive += 1
        return Promise.resolve(runActive as never)
      }
      calls[options.view === 'ended' ? 'runEnded' : 'runArchived'] += 1
      return Promise.resolve(runPage([], 0) as never)
    })
    mockOpList.mockImplementation((_workspace, options) => {
      if (options.view === 'active') {
        calls.opActive += 1
        return Promise.resolve(opActive as never)
      }
      calls[options.view === 'ended' ? 'opEnded' : 'opArchived'] += 1
      return Promise.resolve(opPage([], 0) as never)
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result, unmount } = renderHook(
      () => {
        const run = useActiveRunBatches('ws1')
        const op = useActiveJobBatchOps('ws1')
        useActiveRunBatches('ws1')
        useActiveJobBatchOps('ws1')
        useRunBatchWaterfall('ws1', 'ended', true)
        useRunBatchWaterfall('ws1', 'archived', true)
        useJobBatchOpWaterfall('ws1', 'ended', true)
        useJobBatchOpWaterfall('ws1', 'archived', true)
        return { run, op }
      },
      { wrapper }
    )

    await waitFor(() => {
      expect(calls).toEqual({
        runActive: 1,
        runEnded: 1,
        runArchived: 1,
        opActive: 1,
        opEnded: 1,
        opArchived: 1,
      })
      expect(result.current.run.data?.batches[0]?.run_id).toBe('run-old')
      expect(result.current.op.data?.operations[0]?.id).toBe('op-old')
    })

    runActive = runPage(['run-new'], 1)
    opActive = opPage(['op-new'], 1)
    await act(async () => {
      await Promise.all([
        client.refetchQueries({ queryKey: runBatchKeys.view('ws1', 'active') }),
        client.refetchQueries({
          queryKey: [...jobBatchOpKeys.all('ws1'), 'active'],
        }),
      ])
    })
    expect(client.getQueryData(runBatchKeys.view('ws1', 'active'))).toEqual(
      runActive
    )
    await waitFor(() => {
      expect(calls.runEnded).toBe(2)
      expect(calls.runArchived).toBe(2)
      expect(calls.opEnded).toBe(2)
      expect(calls.opArchived).toBe(2)
    })

    runActive = runPage([], 0)
    await act(async () => {
      await client.refetchQueries({
        queryKey: runBatchKeys.view('ws1', 'active'),
      })
    })
    await waitFor(() => {
      expect(calls.runEnded).toBe(3)
      expect(calls.runArchived).toBe(3)
    })
    expect(calls.opEnded).toBe(2)
    expect(calls.opArchived).toBe(2)

    opActive = opPage([], 0)
    await act(async () => {
      await client.refetchQueries({
        queryKey: [...jobBatchOpKeys.all('ws1'), 'active'],
      })
    })
    await waitFor(() => {
      expect(calls.opEnded).toBe(3)
      expect(calls.opArchived).toBe(3)
    })
    expect(calls.runEnded).toBe(3)
    expect(calls.runArchived).toBe(3)
    expect(calls.runActive).toBe(3)
    expect(calls.opActive).toBe(3)
    unmount()
    client.clear()
  })
})
