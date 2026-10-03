import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createElement, type ReactNode } from 'react'
import { act, render, renderHook, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { WorkspaceStreamStatus } from './WorkspaceStreamStatus'
import { useWorkspaceEvents } from '../hooks/useWorkspaceEvents'
import {
  selectWorkspaceStreamHealth,
  useWorkspaceStreamStore,
} from '../stores/workspaceStreamStore'
import { EventSourceMock } from '../testing/eventSourceMock'
import { createTestQueryClient } from '../testing/testQueryClient'
import * as api from '../api'

vi.mock('../api')

const mockFetchJobsSnapshot = vi.mocked(api.fetchJobsSnapshot)

function resetStore() {
  useWorkspaceStreamStore.setState({
    workspaceId: null,
    status: null,
    everOpened: false,
    attempts: 0,
    staleSince: null,
  })
}

function health(workspaceId = 'ws1') {
  return selectWorkspaceStreamHealth(
    useWorkspaceStreamStore.getState(),
    workspaceId
  )
}

describe('workspaceStreamStore (#720)', () => {
  beforeEach(resetStore)

  it('treats the initial connect as healthy (no flash on page load)', () => {
    useWorkspaceStreamStore.getState().setStatus('ws1', 'connecting')
    expect(health()).toEqual({ kind: 'live' })
  })

  it('marks a drop after open as reconnecting with the stale timestamp', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-04T08:00:00Z'))
    const { setStatus } = useWorkspaceStreamStore.getState()
    setStatus('ws1', 'connecting')
    setStatus('ws1', 'open')
    vi.setSystemTime(new Date('2026-10-04T08:05:00Z'))
    setStatus('ws1', 'connecting')
    vi.setSystemTime(new Date('2026-10-04T08:06:00Z'))
    // 退避中的后续重试不刷新断线时刻。
    setStatus('ws1', 'connecting')
    expect(health()).toEqual({
      kind: 'reconnecting',
      staleSince: new Date('2026-10-04T08:05:00Z').getTime(),
    })
    setStatus('ws1', 'open')
    expect(health()).toEqual({ kind: 'live' })
    vi.useRealTimers()
  })

  it('reports an initial connect that keeps failing as unreachable', () => {
    const { setStatus } = useWorkspaceStreamStore.getState()
    setStatus('ws1', 'connecting')
    setStatus('ws1', 'connecting')
    expect(health()).toEqual({ kind: 'unreachable' })
  })

  it('ignores a late close from a previous workspace', () => {
    const { setStatus } = useWorkspaceStreamStore.getState()
    setStatus('ws1', 'open')
    setStatus('ws2', 'connecting')
    setStatus('ws2', 'connecting')
    setStatus('ws1', 'closed')
    expect(health('ws2')).toEqual({ kind: 'unreachable' })
    expect(health('ws1')).toEqual({ kind: 'live' })
  })
})

describe('useWorkspaceEvents → WorkspaceStreamStatus (#720)', () => {
  const originalEventSource = globalThis.EventSource
  let client = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)

  beforeEach(() => {
    resetStore()
    client = createTestQueryClient()
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    vi.clearAllMocks()
    mockFetchJobsSnapshot.mockResolvedValue({
      workspace_id: 'ws1',
      revision: 0,
      stats: {},
      jobs: [],
      next_cursor: null,
    })
    vi.mocked(api.fetchJobFacets).mockResolvedValue({
      workspace_id: 'ws1',
      total: 0,
      status_counts: {},
      version_counts: {},
      node_counts: {},
    })
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
    vi.useRealTimers()
  })

  it('shows the reconnecting notice on SSE drop and recovers on reconnect', async () => {
    vi.useFakeTimers()
    const hook = renderHook(() => useWorkspaceEvents('ws1'), { wrapper })
    render(<WorkspaceStreamStatus workspaceId="ws1" />)

    await act(async () => {
      EventSourceMock.instances[0].onopen?.()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.queryByTestId('workspace-stream-status')).toBeNull()
    expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(1)

    // 断线：realtime 层退避 1s 后重新建连（发 connecting）。
    await act(async () => {
      EventSourceMock.instances[0].onerror?.()
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(EventSourceMock.instances).toHaveLength(2)
    const notice = screen.getByTestId('workspace-stream-status')
    expect(notice.textContent).toContain('实时连接中断，正在重连')
    expect(notice.textContent).toContain('可能已过时')

    // 重连成功：提示消失、快照自动重拉，无需手动刷新。
    await act(async () => {
      EventSourceMock.instances[1].onopen?.()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.queryByTestId('workspace-stream-status')).toBeNull()
    expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(2)

    hook.unmount()
    expect(useWorkspaceStreamStore.getState().workspaceId).toBeNull()
  })

  it('renders nothing for a different workspace', () => {
    useWorkspaceStreamStore.getState().setStatus('ws1', 'open')
    useWorkspaceStreamStore.getState().setStatus('ws1', 'connecting')
    render(<WorkspaceStreamStatus workspaceId="ws2" />)
    expect(screen.queryByTestId('workspace-stream-status')).toBeNull()
  })
})
