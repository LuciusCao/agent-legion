import { renderHook, waitFor, act } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getAgentCatalog } from '../../../api/agentCatalogApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { createTestQueryClient } from '../../../testing/testQueryClient'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { useAgentCatalog } from './useAgentCatalog'

vi.mock('../../../api/agentCatalogApi', () => ({ getAgentCatalog: vi.fn() }))

const mockGetCatalog = vi.mocked(getAgentCatalog)

const agent: AgentDefinition = {
  id: 'agent-v1',
  capability: 'fetch_items',
  runtime: 'velites',
  skill: 'demo/skill',
}

function wrapper(client: QueryClient) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children)
  }
}

describe('useAgentCatalog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns the agents from the catalog', async () => {
    mockGetCatalog.mockResolvedValue({ agents: [agent] })
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(createTestQueryClient()),
    })

    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    expect(result.current.agents[0]?.id).toBe('agent-v1')
    expect(result.current.loadError).toBe(false)
  })

  it('keeps the error state and recovers via retry instead of swallowing failures', async () => {
    mockGetCatalog.mockRejectedValueOnce(new Error('boom'))
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(createTestQueryClient()),
    })

    await waitFor(() => expect(result.current.loadError).toBe(true))
    expect(result.current.agents).toEqual([])

    mockGetCatalog.mockResolvedValue({ agents: [agent] })
    await act(async () => {
      await result.current.retry()
    })

    await waitFor(() => expect(result.current.loadError).toBe(false))
    expect(result.current.agents).toHaveLength(1)
    expect(mockGetCatalog).toHaveBeenCalledTimes(2)
  })

  it('refetches when the studioAgentCatalog key is invalidated', async () => {
    mockGetCatalog.mockResolvedValue({ agents: [agent] })
    const client = createTestQueryClient()
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(client),
    })

    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    expect(mockGetCatalog).toHaveBeenCalledTimes(1)

    await act(async () => {
      await client.invalidateQueries({
        queryKey: extraQueryKeys.studioAgentCatalog('ws1'),
      })
    })

    expect(mockGetCatalog).toHaveBeenCalledTimes(2)
  })
})
