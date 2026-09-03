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
    // #426 review P2：失败期绑定解析同样不可信——bindingStatus 走 error
    // 而非 ready（不渲染可操作表单）。
    expect(result.current.bindingStatus).toBe('error')

    mockGetCatalog.mockResolvedValue({ agents: [agent] })
    await act(async () => {
      await result.current.retry()
    })

    await waitFor(() => expect(result.current.loadError).toBe(false))
    expect(result.current.agents).toHaveLength(1)
    // retry 成功取回数据后回到 ready——门控放行。
    await waitFor(() => expect(result.current.bindingStatus).toBe('ready'))
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

  // #426 review P2 → codex P2 修正：bindingStatus 是内联 Agent 编辑器的
  // 渲染门控，只按 published 目录的 settle 态计算——目录在途时 agentId
  // 可能只是 draft 回落先行（settle 后会被同 capability 的 published
  // Agent 替换），门控不放行。
  it('reports pending until the catalog settles, then ready', async () => {
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

    // 目录在途：即使定义列表已返回，绑定解析仍不可信（draft 回落先行）。
    await waitFor(() => expect(result.current.bindingStatus).toBe('pending'))

    resolveCatalog({ agents: [agent] })
    await waitFor(() => expect(result.current.bindingStatus).toBe('ready'))
  })

  // #426 codex P2 修正：definitions 在途不再拖住 bindingStatus——门控关心
  // 的是「published ?? draft」何时成为终态，这只取决于目录；published 命中
  // 时 AgentEditor 按 ID 加载详情不依赖 definitions 列表，draft 回落场景
  // definitions 必已返回（agentId 有值的前提），其失败走 loadError 横幅。
  it('reports ready once the catalog returns even while the definitions query is still in flight', async () => {
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

    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    // 目录已 settle：published 命中即终态，门控放行（不等待定义列表）。
    expect(result.current.bindingStatus).toBe('ready')

    resolveDefinitions({ agents: [] })
    await waitFor(() => expect(result.current.definitions).toEqual([]))
    expect(result.current.bindingStatus).toBe('ready')
  })

  // 目录失败与「确认无 published」必须区分：失败 → error（不落回可操作
  // 表单）；definitions 失败不改变目录 settle 结论，走 loadError 横幅。
  it('reports error only when the catalog itself fails without data', async () => {
    mockGetCatalog.mockRejectedValue(new Error('catalog boom'))
    mockFetchDefinitions.mockResolvedValue({ agents: [] })
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(createTestQueryClient()),
    })

    await waitFor(() => expect(result.current.bindingStatus).toBe('error'))
    expect(result.current.loadError).toBe(true)
  })

  it('stays ready when only the definitions query fails (catalog error surfaces via loadError)', async () => {
    mockGetCatalog.mockResolvedValue({ agents: [agent] })
    mockFetchDefinitions.mockRejectedValue(new Error('definitions boom'))
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(createTestQueryClient()),
    })

    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    // 目录 settle 结论不受 definitions 失败影响；失败由 loadError 暴露。
    expect(result.current.bindingStatus).toBe('ready')
    expect(result.current.loadError).toBe(true)
  })
})
