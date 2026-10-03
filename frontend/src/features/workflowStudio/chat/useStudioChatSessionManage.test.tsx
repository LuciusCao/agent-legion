/**
 * #872 会话管理动作：删除落地时先从 sessions 缓存摘掉再按**当前**选中判定
 * 清空（R1 P2：不用发起时快照）——会话记忆回落不得把被删会话重新选回来；
 * 删除在途时切换会话两种顺序都要正确；改名后刷新列表。
 */
import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { queryKeys } from '../../../lib/queryKeys'
import * as manageApi from './studioChatSessionManageApi'
import { useStudioChatSessionManage } from './useStudioChatSessionManage'

vi.mock('./studioChatSessionManageApi')
const mockApi = vi.mocked(manageApi)

function setup(initialActive: string | null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const key = queryKeys.studioChatSessions('ws1')
  client.setQueryData(key, [{ id: 's1' }, { id: 's2' }, { id: 's3' }])
  const cachedAtClear: string[][] = []
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const { result } = renderHook(
    () => {
      const [active, setActive] = useState<string | null>(initialActive)
      const manage = useStudioChatSessionManage('ws1', (update) => {
        cachedAtClear.push(
          (client.getQueryData(key) as { id: string }[]).map((r) => r.id)
        )
        setActive(update)
      })
      return { active, setActive, manage }
    },
    { wrapper }
  )
  return { result, client, key, cachedAtClear }
}

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

describe('useStudioChatSessionManage', () => {
  it('deleting the active session prunes the cache before clearing selection', async () => {
    mockApi.deleteStudioChatSession.mockResolvedValue(undefined)
    const { result, cachedAtClear } = setup('s1')
    await act(() => result.current.manage.remove('s1'))
    expect(mockApi.deleteStudioChatSession).toHaveBeenCalledWith('ws1', 's1')
    expect(result.current.active).toBeNull()
    // 清空那一刻缓存里已没有被删会话（记忆回落读的就是它）。
    expect(cachedAtClear).toEqual([['s2', 's3']])
  })

  it('switching away while the delete is in flight keeps the new selection', async () => {
    const pending = deferred()
    mockApi.deleteStudioChatSession.mockReturnValue(pending.promise)
    const { result } = setup('s1')
    let removal!: Promise<void>
    act(() => {
      removal = result.current.manage.remove('s1')
    })
    act(() => result.current.setActive('s2'))
    await act(async () => {
      pending.resolve()
      await removal
    })
    expect(result.current.active).toBe('s2')
  })

  it('switching onto the session being deleted clears it on landing', async () => {
    const pending = deferred()
    mockApi.deleteStudioChatSession.mockReturnValue(pending.promise)
    const { result } = setup('s2')
    let removal!: Promise<void>
    act(() => {
      removal = result.current.manage.remove('s1')
    })
    act(() => result.current.setActive('s1'))
    await act(async () => {
      pending.resolve()
      await removal
    })
    expect(result.current.active).toBeNull()
  })

  it('deleting another session keeps the selection', async () => {
    mockApi.deleteStudioChatSession.mockResolvedValue(undefined)
    const { result, client, key } = setup('s2')
    await act(() => result.current.manage.remove('s1'))
    expect(result.current.active).toBe('s2')
    expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  })

  it('a failed delete leaves cache and selection untouched', async () => {
    mockApi.deleteStudioChatSession.mockRejectedValue(new Error('boom'))
    const { result, client, key, cachedAtClear } = setup('s1')
    await act(async () => {
      await expect(result.current.manage.remove('s1')).rejects.toThrow('boom')
    })
    expect(result.current.active).toBe('s1')
    expect(cachedAtClear).toEqual([])
    expect(
      (client.getQueryData(key) as { id: string }[]).map((r) => r.id)
    ).toEqual(['s1', 's2', 's3'])
  })

  it('rename calls the API and refreshes the list', async () => {
    mockApi.renameStudioChatSession.mockResolvedValue(
      {} as Awaited<ReturnType<typeof manageApi.renameStudioChatSession>>
    )
    const { result, client, key } = setup('s1')
    await act(() => result.current.manage.rename('s1', '新名字'))
    expect(mockApi.renameStudioChatSession).toHaveBeenCalledWith(
      'ws1',
      's1',
      '新名字'
    )
    expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  })
})
