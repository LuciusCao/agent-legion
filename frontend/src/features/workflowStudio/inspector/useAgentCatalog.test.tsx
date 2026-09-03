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
    // #426 review P2：失败且无数据的查询让绑定解析不可信——settle 信号
    // 标记 catalogFailed（下游门控走 error 而非 ready，不出可操作表单）。
    expect(result.current.settle.catalogFailed).toBe(true)

    mockGetCatalog.mockResolvedValue({ agents: [agent] })
    await act(async () => {
      await result.current.retry()
    })

    await waitFor(() => expect(result.current.loadError).toBe(false))
    expect(result.current.agents).toHaveLength(1)
    // retry 成功取回数据后 settle 回到干净态——门控放行。
    await waitFor(() => expect(result.current.settle.catalogFailed).toBe(false))
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

  // #426 review P2 → codex 终轮 P2：本 hook 是 workspace 级，不再预折叠
  // bindingStatus，而是把两份查询的 settle 信号（settle 嵌套对象）下发，
  // 由节点级（agentBindingStatus.bindingStatus + useCapabilityAgent 命中）
  // 组合出门控。目录在途时无论 definitions 是否已返回，catalogSettled
  // 都是 false——节点级据此保持 pending（draft 回落先行不放行）。
  it('reports catalogSettled=false while the catalog is loading even if definitions returned', async () => {
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

    await waitFor(() =>
      expect(result.current.settle.catalogSettled).toBe(false)
    )
    // definitions 的 mock 已 resolve：等它的 settle 标志翻转（agents 字段
    // 在 pending 期就是 []，不能拿它当 settle 信号）。
    await waitFor(() =>
      expect(result.current.settle.definitionsSettled).toBe(true)
    )

    resolveCatalog({ agents: [agent] })
    await waitFor(() => expect(result.current.settle.catalogSettled).toBe(true))
  })

  // #426 codex 终轮 P2（命中侧）：catalog 已返回即 catalogSettled=true，与
  // definitions 在途无关——节点级对 published 命中的 capability 据此直接
  // ready（AgentEditor 按 ID 加载详情不依赖列表，definitions 失败走
  // loadError 横幅）。settle 只反映查询状态，不含 capability 语义。
  it('reports catalogSettled=true once the catalog returns even while definitions are in flight', async () => {
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
    expect(result.current.settle.catalogSettled).toBe(true)
    expect(result.current.settle.definitionsSettled).toBe(false)

    resolveDefinitions({ agents: [] })
    // agents 字段 pending 期就是 []，settle 以查询状态为准。
    await waitFor(() =>
      expect(result.current.settle.definitionsSettled).toBe(true)
    )
    expect(result.current.definitions).toEqual([])
  })

  // 目录失败与「已返回」必须区分：失败且无数据 → catalogFailed=true（下游
  // 门控走 error，不落回可操作表单）；definitions 失败只并入 loadError
  // 横幅，是否阻断门控由节点级按 capability 命中决定。
  it('marks catalogFailed only when the catalog itself fails without data', async () => {
    mockGetCatalog.mockRejectedValue(new Error('catalog boom'))
    mockFetchDefinitions.mockResolvedValue({ agents: [] })
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(createTestQueryClient()),
    })

    await waitFor(() => expect(result.current.settle.catalogFailed).toBe(true))
    expect(result.current.loadError).toBe(true)
  })

  it('keeps settle clean when only the definitions query fails (error surfaces via loadError)', async () => {
    mockGetCatalog.mockResolvedValue({ agents: [agent] })
    mockFetchDefinitions.mockRejectedValue(new Error('definitions boom'))
    const { result } = renderHook(() => useAgentCatalog('ws1'), {
      wrapper: wrapper(createTestQueryClient()),
    })

    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    expect(result.current.settle.catalogFailed).toBe(false)
    expect(result.current.settle.definitionsFailed).toBe(true)
    expect(result.current.loadError).toBe(true)
  })
})
