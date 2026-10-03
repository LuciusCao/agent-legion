/**
 * #872 会话管理动作：删除当前会话时先从 sessions 缓存摘掉再清空选中——
 * 会话记忆回落不得把被删会话重新选回来；改名后刷新列表。
 */
import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { queryKeys } from '../../../lib/queryKeys'
import * as manageApi from './studioChatSessionManageApi'
import { useStudioChatSessionManage } from './useStudioChatSessionManage'

vi.mock('./studioChatSessionManageApi')
const mockApi = vi.mocked(manageApi)

function setup(activeSessionId: string | null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const key = queryKeys.studioChatSessions('ws1')
  client.setQueryData(key, [{ id: 's1' }, { id: 's2' }])
  const clearActive = vi.fn(() => {
    // 清空选中那一刻，缓存里必须已经没有被删会话（记忆回落读的就是它）。
    expect(
      (client.getQueryData(key) as { id: string }[]).map((r) => r.id)
    ).toEqual(['s2'])
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const { result } = renderHook(
    () => useStudioChatSessionManage('ws1', activeSessionId, clearActive),
    { wrapper }
  )
  return { result, clearActive, client, key }
}

describe('useStudioChatSessionManage', () => {
  it('deleting the active session prunes the cache before clearing selection', async () => {
    mockApi.deleteStudioChatSession.mockResolvedValue(undefined)
    const { result, clearActive } = setup('s1')
    await act(() => result.current.remove('s1'))
    expect(mockApi.deleteStudioChatSession).toHaveBeenCalledWith('ws1', 's1')
    expect(clearActive).toHaveBeenCalledTimes(1)
  })

  it('deleting another session keeps the selection', async () => {
    mockApi.deleteStudioChatSession.mockResolvedValue(undefined)
    const { result, clearActive, client, key } = setup('s2')
    await act(() => result.current.remove('s1'))
    expect(clearActive).not.toHaveBeenCalled()
    expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  })

  it('a failed delete leaves cache and selection untouched', async () => {
    mockApi.deleteStudioChatSession.mockRejectedValue(new Error('boom'))
    const { result, clearActive, client, key } = setup('s1')
    await expect(result.current.remove('s1')).rejects.toThrow('boom')
    expect(clearActive).not.toHaveBeenCalled()
    expect(
      (client.getQueryData(key) as { id: string }[]).map((r) => r.id)
    ).toEqual(['s1', 's2'])
  })

  it('rename calls the API and refreshes the list', async () => {
    mockApi.renameStudioChatSession.mockResolvedValue(
      {} as Awaited<ReturnType<typeof manageApi.renameStudioChatSession>>
    )
    const { result, client, key } = setup('s1')
    await act(() => result.current.rename('s1', '新名字'))
    expect(mockApi.renameStudioChatSession).toHaveBeenCalledWith(
      'ws1',
      's1',
      '新名字'
    )
    expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  })
})
