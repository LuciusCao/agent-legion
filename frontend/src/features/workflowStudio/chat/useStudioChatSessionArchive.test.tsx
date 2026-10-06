/**
 * #924 归档动作：归档当前会话与删除同一套收尾（先摘默认列表缓存、再按落地
 * 时的选中清空）；取消归档不改选中、刷新两份列表（归档视图 key 挂在
 * sessions key 之下，前缀失效一并刷新）；归档视图走 ?archived=true 查询。
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { queryKeys } from '../../../lib/queryKeys'
import { studioChatRetentionKey } from './studioChatRetention'
import * as archiveApi from './studioChatSessionArchiveApi'
import {
  archivedStudioChatSessionsKey,
  useStudioChatSessionArchive,
} from './useStudioChatSessionArchive'

vi.mock('./studioChatSessionArchiveApi')
const mockApi = vi.mocked(archiveApi)

function setup(initialActive: string | null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const key = queryKeys.studioChatSessions('ws1')
  client.setQueryData(key, [{ id: 's1' }, { id: 's2' }])
  const cachedAtClear: string[][] = []
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const { result } = renderHook(
    () => {
      const [active, setActive] = useState<string | null>(initialActive)
      const archive = useStudioChatSessionArchive('ws1', (update) => {
        cachedAtClear.push(
          (client.getQueryData(key) as { id: string }[]).map((r) => r.id)
        )
        setActive(update)
      })
      return { active, archive }
    },
    { wrapper }
  )
  return { result, client, key, cachedAtClear }
}

const EMPTY = { sessions: [], retentionDays: 0 }

const RECORD = {} as Awaited<
  ReturnType<typeof archiveApi.archiveStudioChatSession>
>

describe('useStudioChatSessionArchive', () => {
  it('loads the archive view list', async () => {
    mockApi.fetchArchivedStudioChatSessions.mockResolvedValue({
      sessions: [{ id: 's9' } as never],
      retentionDays: 30,
    })
    const { result } = setup('s1')
    await waitFor(() =>
      expect(result.current.archive.archivedSessions).toEqual([{ id: 's9' }])
    )
    // #1041：归档视图响应带回实例保留天数。
    expect(result.current.archive.retentionDays).toBe(30)
    expect(mockApi.fetchArchivedStudioChatSessions).toHaveBeenCalledWith('ws1')
  })

  it('retention is unknown (null, not "off") while neither list answered', () => {
    mockApi.fetchArchivedStudioChatSessions.mockReturnValue(
      new Promise(() => undefined)
    )
    const { result } = setup('s1')
    expect(result.current.archive.retentionDays).toBeNull()
  })

  it('a failed archive view keeps retention unknown', async () => {
    mockApi.fetchArchivedStudioChatSessions.mockRejectedValue(new Error('500'))
    const { result, client } = setup('s1')
    await waitFor(() =>
      expect(
        client.getQueryState(archivedStudioChatSessionsKey('ws1'))?.status
      ).toBe('error')
    )
    expect(result.current.archive.retentionDays).toBeNull()
  })

  it('the default list response supplies retention before the archive view', async () => {
    mockApi.fetchArchivedStudioChatSessions.mockReturnValue(
      new Promise(() => undefined)
    )
    const { result, client } = setup('s1')
    act(() => {
      client.setQueryData(studioChatRetentionKey('ws1'), 14)
    })
    await waitFor(() => expect(result.current.archive.retentionDays).toBe(14))
  })

  it('archiving the active session prunes the cache before clearing selection', async () => {
    mockApi.fetchArchivedStudioChatSessions.mockResolvedValue(EMPTY)
    mockApi.archiveStudioChatSession.mockResolvedValue(RECORD)
    const { result, client, cachedAtClear } = setup('s1')
    const archivedKey = archivedStudioChatSessionsKey('ws1')
    await waitFor(() =>
      expect(client.getQueryState(archivedKey)?.status).toBe('success')
    )
    const fetchesBefore =
      mockApi.fetchArchivedStudioChatSessions.mock.calls.length
    await act(() => result.current.archive.archive('s1'))
    expect(mockApi.archiveStudioChatSession).toHaveBeenCalledWith('ws1', 's1')
    expect(result.current.active).toBeNull()
    expect(cachedAtClear).toEqual([['s2']])
    // 归档视图（有观察者）随 sessions 前缀失效立即重拉。
    await waitFor(() =>
      expect(mockApi.fetchArchivedStudioChatSessions.mock.calls.length).toBe(
        fetchesBefore + 1
      )
    )
  })

  it('unarchive keeps the selection and refreshes both lists', async () => {
    mockApi.fetchArchivedStudioChatSessions.mockResolvedValue(EMPTY)
    mockApi.unarchiveStudioChatSession.mockResolvedValue(RECORD)
    const { result, client, key } = setup('s2')
    await act(() => result.current.archive.unarchive('s9'))
    expect(mockApi.unarchiveStudioChatSession).toHaveBeenCalledWith('ws1', 's9')
    expect(result.current.active).toBe('s2')
    expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  })

  it('a failed archive leaves cache and selection untouched', async () => {
    mockApi.fetchArchivedStudioChatSessions.mockResolvedValue(EMPTY)
    mockApi.archiveStudioChatSession.mockRejectedValue(new Error('boom'))
    const { result, client, key, cachedAtClear } = setup('s1')
    await act(async () => {
      await expect(result.current.archive.archive('s1')).rejects.toThrow('boom')
    })
    expect(result.current.active).toBe('s1')
    expect(cachedAtClear).toEqual([])
    expect(
      (client.getQueryData(key) as { id: string }[]).map((r) => r.id)
    ).toEqual(['s1', 's2'])
  })
})
