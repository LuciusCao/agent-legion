import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import {
  useCreateWorkspace,
  useUpdateWorkspace,
  useDeleteWorkspace,
} from './useWorkspaceMutations'
import { createTestQueryClient } from '../testing/testQueryClient'
import { makeWorkspace } from '../testing/workspaceFixtures'
import { queryKeys } from '../lib/queryKeys'
import { createWorkspace, updateWorkspace, deleteWorkspace } from '../api'

vi.mock('../api', () => ({
  createWorkspace: vi.fn(),
  updateWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
}))

const mockCreateWorkspace = vi.mocked(createWorkspace)
const mockUpdateWorkspace = vi.mocked(updateWorkspace)
const mockDeleteWorkspace = vi.mocked(deleteWorkspace)

describe('useWorkspaceMutations', () => {
  let testClient = createTestQueryClient()

  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)

  beforeEach(() => {
    testClient = createTestQueryClient()
    vi.clearAllMocks()
  })

  it('createWorkspace calls the API and invalidates the workspaces list', async () => {
    const ws = makeWorkspace({ id: 'ws-new', name: '新空间' })
    mockCreateWorkspace.mockResolvedValue(ws)
    const invalidateSpy = vi.spyOn(testClient, 'invalidateQueries')
    const { result } = renderHook(() => useCreateWorkspace(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({
        name: '新空间',
        workflowMode: 'demo',
      })
    })

    expect(mockCreateWorkspace).toHaveBeenCalledWith('新空间', 'demo')
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.workspaces(),
    })
  })

  it('createWorkspace forwards blank workflow mode', async () => {
    const ws = makeWorkspace({ id: 'ws-blank', name: '空白空间' })
    mockCreateWorkspace.mockResolvedValue(ws)
    const { result } = renderHook(() => useCreateWorkspace(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({
        name: '空白空间',
        workflowMode: 'blank',
      })
    })

    expect(mockCreateWorkspace).toHaveBeenCalledWith('空白空间', 'blank')
  })

  it('createWorkspace defaults to the blank canvas', async () => {
    const ws = makeWorkspace({ id: 'ws-blank', name: '空白空间' })
    mockCreateWorkspace.mockResolvedValue(ws)
    const { result } = renderHook(() => useCreateWorkspace(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({ name: '空白空间' })
    })

    expect(mockCreateWorkspace).toHaveBeenCalledWith('空白空间', 'blank')
  })

  it('createWorkspace propagates API errors', async () => {
    mockCreateWorkspace.mockRejectedValue(new Error('duplicate name'))
    const { result } = renderHook(() => useCreateWorkspace(), { wrapper })

    await expect(
      result.current.mutateAsync({
        name: '新空间',
      })
    ).rejects.toThrow('duplicate name')
  })

  it('updateWorkspace calls the API and invalidates the workspaces list', async () => {
    const ws = makeWorkspace({ id: 'ws1', name: '改名后' })
    mockUpdateWorkspace.mockResolvedValue(ws)
    const invalidateSpy = vi.spyOn(testClient, 'invalidateQueries')
    const { result } = renderHook(() => useUpdateWorkspace(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync({
        id: 'ws1',
        fields: { name: '改名后' },
      })
    })

    expect(mockUpdateWorkspace).toHaveBeenCalledWith('ws1', { name: '改名后' })
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.workspaces(),
    })
  })

  it('deleteWorkspace calls the API and invalidates the workspaces list', async () => {
    mockDeleteWorkspace.mockResolvedValue(undefined)
    const invalidateSpy = vi.spyOn(testClient, 'invalidateQueries')
    const { result } = renderHook(() => useDeleteWorkspace(), { wrapper })

    await act(async () => {
      await result.current.mutateAsync('ws1')
    })

    expect(mockDeleteWorkspace).toHaveBeenCalledWith('ws1')
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: queryKeys.workspaces(),
      })
    })
  })
})
