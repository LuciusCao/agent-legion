import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { useWorkspaceStats } from './useWorkspaceStats'
import { createTestQueryClient } from '../testing/testQueryClient'
import { fetchWorkspaceStats } from '../api'
import type { WorkspaceStats } from '../types/workspaceTypes'

vi.mock('../api', () => ({
  fetchWorkspaceStats: vi.fn(),
}))

const mockFetchWorkspaceStats = vi.mocked(fetchWorkspaceStats)

const stats = {
  workspace_id: 'ws1',
  workflow_key: 'question_content',
  job_stats: { running: 1 },
} as unknown as WorkspaceStats

describe('useWorkspaceStats', () => {
  let testClient = createTestQueryClient()

  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)

  beforeEach(() => {
    testClient = createTestQueryClient()
    mockFetchWorkspaceStats.mockReset()
    mockFetchWorkspaceStats.mockResolvedValue(stats)
  })

  it('fetches stats for the given workspace', async () => {
    const { result } = renderHook(() => useWorkspaceStats('ws1'), { wrapper })

    await waitFor(() => {
      expect(result.current.data).toEqual(stats)
    })
    expect(mockFetchWorkspaceStats).toHaveBeenCalledWith('ws1')
  })

  it('stays disabled when workspaceId is undefined', async () => {
    const { result } = renderHook(() => useWorkspaceStats(undefined), {
      wrapper,
    })

    await flushMicrotasks()
    expect(mockFetchWorkspaceStats).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
  })

  it('keeps data undefined when the fetch fails', async () => {
    mockFetchWorkspaceStats.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useWorkspaceStats('ws1'), { wrapper })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })
    expect(result.current.data).toBeUndefined()
  })
})

async function flushMicrotasks() {
  await new Promise((resolve) => setTimeout(resolve, 20))
}
