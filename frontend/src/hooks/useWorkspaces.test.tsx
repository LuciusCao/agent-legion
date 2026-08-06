import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createElement, Fragment, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { useWorkspaces, useCurrentWorkspace } from './useWorkspaces'
import { createTestQueryClient } from '../testing/testQueryClient'
import { makeWorkspace } from '../testing/workspaceFixtures'
import { fetchWorkspaces } from '../api'

vi.mock('../api', () => ({
  fetchWorkspaces: vi.fn(),
}))

const mockFetchWorkspaces = vi.mocked(fetchWorkspaces)

const ws1 = makeWorkspace({ id: 'ws1', name: '测试空间' })
const ws2 = makeWorkspace({ id: 'ws2', name: '另一个空间' })

describe('useWorkspaces', () => {
  let testClient = createTestQueryClient()

  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)

  // testing/TestMemoryRouter 内嵌 TestQueryProvider（每次挂载独立 client）
  // 并带 future flags，路由用例直接复用。
  const routeWrapper =
    (entry: string) =>
    ({ children }: { children: ReactNode }) =>
      createElement(
        MemoryRouter,
        { initialEntries: [entry] },
        createElement(
          Routes,
          null,
          createElement(Route, {
            path: '/workspaces/:workspaceId',
            element: createElement(Fragment, null, children),
          })
        )
      )

  beforeEach(() => {
    testClient = createTestQueryClient()
    mockFetchWorkspaces.mockReset()
    mockFetchWorkspaces.mockResolvedValue({ workspaces: [ws1, ws2] })
  })

  it('fetches and returns the workspace list', async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper })

    await waitFor(() => {
      expect(result.current.data).toHaveLength(2)
    })
    expect(mockFetchWorkspaces).toHaveBeenCalledTimes(1)
    expect(result.current.data?.[0].name).toBe('测试空间')
  })

  it('useCurrentWorkspace derives the workspace from the route param', async () => {
    const { result } = renderHook(() => useCurrentWorkspace(), {
      wrapper: routeWrapper('/workspaces/ws2'),
    })

    await waitFor(() => {
      expect(result.current?.id).toBe('ws2')
    })
    expect(result.current?.name).toBe('另一个空间')
  })

  it('useCurrentWorkspace returns null when the id is not in the list', async () => {
    const { result } = renderHook(() => useCurrentWorkspace(), {
      wrapper: routeWrapper('/workspaces/missing'),
    })

    await waitFor(() => {
      expect(mockFetchWorkspaces).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(result.current).toBeNull()
    })
  })
})
