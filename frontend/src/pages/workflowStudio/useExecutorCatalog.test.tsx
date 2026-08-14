import { renderHook, waitFor, act } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getExecutorCatalog } from '../../api/executorApi'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { createTestQueryClient } from '../../testing/testQueryClient'
import type { ExecutorDefinition } from '../../types/executorTypes'
import { useExecutorCatalog } from './useExecutorCatalog'

vi.mock('../../api/executorApi', () => ({ getExecutorCatalog: vi.fn() }))

const mockGetCatalog = vi.mocked(getExecutorCatalog)

const executor: ExecutorDefinition = {
  id: 'code-default',
  kind: 'code',
  global_capacity: 16,
  capabilities: ['fetch_questions'],
  capability_details: [
    { name: 'fetch_questions', path: 'workflow_nodes/fetch_questions.py' },
  ],
}

function wrapper(client: QueryClient) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children)
  }
}

describe('useExecutorCatalog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns executors and agents from the catalog', async () => {
    mockGetCatalog.mockResolvedValue({ executors: [executor], agents: [] })
    const { result } = renderHook(() => useExecutorCatalog(), {
      wrapper: wrapper(createTestQueryClient()),
    })

    await waitFor(() => expect(result.current.executors).toHaveLength(1))
    expect(result.current.executors[0]?.id).toBe('code-default')
    expect(result.current.loadError).toBe(false)
  })

  it('keeps the error state and recovers via retry instead of swallowing failures', async () => {
    mockGetCatalog.mockRejectedValueOnce(new Error('boom'))
    const { result } = renderHook(() => useExecutorCatalog(), {
      wrapper: wrapper(createTestQueryClient()),
    })

    await waitFor(() => expect(result.current.loadError).toBe(true))
    expect(result.current.executors).toEqual([])

    mockGetCatalog.mockResolvedValue({ executors: [executor], agents: [] })
    await act(async () => {
      await result.current.retry()
    })

    await waitFor(() => expect(result.current.loadError).toBe(false))
    expect(result.current.executors).toHaveLength(1)
    expect(mockGetCatalog).toHaveBeenCalledTimes(2)
  })

  it('refetches when the studioExecutorCatalog key is invalidated', async () => {
    mockGetCatalog.mockResolvedValue({ executors: [executor], agents: [] })
    const client = createTestQueryClient()
    const { result } = renderHook(() => useExecutorCatalog(), {
      wrapper: wrapper(client),
    })

    await waitFor(() => expect(result.current.executors).toHaveLength(1))
    expect(mockGetCatalog).toHaveBeenCalledTimes(1)

    await act(async () => {
      await client.invalidateQueries({
        queryKey: extraQueryKeys.studioExecutorCatalog(),
      })
    })

    expect(mockGetCatalog).toHaveBeenCalledTimes(2)
  })
})
