import { renderHook, waitFor, act } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getAgentCatalog } from '../../../api/agentCatalogApi'
import { fetchAgentDefinitions } from '../../../api/agentDefinitions'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { createTestQueryClient } from '../../../testing/testQueryClient'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { useAgentCatalog } from './useAgentCatalog'

vi.mock('../../../api/agentCatalogApi', () => ({ getAgentCatalog: vi.fn() }))

// #426 review P2：useAgentCatalog 同时消费 agent-definitions（含 draft 的
// 绑定回落数据源），mock 掉避免 jsdom 里发真实请求。
vi.mock('../../../api/agentDefinitions', () => ({
  fetchAgentDefinitions: vi.fn(),
}))

const mockGetCatalog = vi.mocked(getAgentCatalog)
const mockFetchDefinitions = vi.mocked(fetchAgentDefinitions)

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
    mockFetchDefinitions.mockResolvedValue({ agents: [] })
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

  // #426 review P2：bindingStatus 是内联 Agent 编辑器的渲染门控——任一
  // 查询（目录 / 含 draft 的定义列表）未 settle 时 agentId=null 只是
  // 「未知」，不得当「未绑定」渲染创建表单。
  it('reports pending until both the catalog and the definitions settle, then ready', async () => {
    let resolveCatalog: (value: {
      agents: AgentDefinition[]
    }) => void = () => {}
    mockGetCatalog.mockReturnValue(
      new Promise((resolve) => {
        resolveCatalog = resolve
      }) as ReturnType<typeof mockGetCatalog>
    )
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(createTestQueryClient()),
    })

    // 目录在途：即使定义列表已返回，绑定解析仍不可信。
    await waitFor(() => expect(result.current.bindingStatus).toBe('pending'))

    resolveCatalog({ agents: [agent] })
    await waitFor(() => expect(result.current.bindingStatus).toBe('ready'))
  })

  it('stays pending while the definitions query is in flight even after the catalog returns', async () => {
    mockGetCatalog.mockResolvedValue({ agents: [agent] })
    let resolveDefinitions: (value: { agents: never[] }) => void = () => {}
    mockFetchDefinitions.mockReturnValue(
      new Promise((resolve) => {
        resolveDefinitions = resolve
      }) as ReturnType<typeof fetchAgentDefinitions>
    )
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(createTestQueryClient()),
    })

    // draft-only Agent 的回落也参与绑定解析：定义列表未返回时不算 ready。
    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    expect(result.current.bindingStatus).toBe('pending')

    resolveDefinitions({ agents: [] })
    await waitFor(() => expect(result.current.bindingStatus).toBe('ready'))
  })

  // 查询失败与「确认未绑定」必须区分：失败 → error（不落回可操作表单）。
  it('reports error when either query fails without data', async () => {
    mockGetCatalog.mockResolvedValue({ agents: [agent] })
    mockFetchDefinitions.mockRejectedValue(new Error('definitions boom'))
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(createTestQueryClient()),
    })

    await waitFor(() => expect(result.current.bindingStatus).toBe('error'))
    expect(result.current.loadError).toBe(true)
  })
})
