import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { useJobDetail } from './useJobDetail'

/**
 * #965 档位迁移：awaiting_approval 降频 30s、终态停轮询；本页操作（审批 /
 * 重跑）成功后 refetch 让新状态立即落地，档位恢复 5s。
 */

let currentDetail: { status: string; nodeStatus: string }

function detailBody() {
  return {
    job: {
      id: 'j1',
      workspace_id: 'ws1',
      source_id: 'Q1',
      source_type: 'knowledge',
      title: 'Job',
      status: currentDetail.status,
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
    },
    nodes: [
      {
        id: 1,
        job_id: 'j1',
        node_key: 'gate',
        label: '审批',
        status: currentDetail.nodeStatus,
        capability: 'approval',
        after: [],
        inputs: [],
        outputs: [],
        error_message: '',
      },
    ],
    runs: [],
    artifacts: [],
  }
}

function createFetchMock() {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (url === '/api/jobs/j1' && method === 'GET') {
      return Promise.resolve({ ok: true, json: async () => detailBody() })
    }
    if (method === 'POST' && url.endsWith('/approval')) {
      currentDetail = { status: 'running', nodeStatus: 'completed' }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    }
    if (method === 'POST' && url.endsWith('/rerun')) {
      currentDetail = { status: 'queued', nodeStatus: 'ready' }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    }
    return Promise.resolve({ ok: true, json: async () => ({}) })
  })
}

function detailGets(fetchMock: ReturnType<typeof createFetchMock>) {
  return fetchMock.mock.calls.filter(
    ([url, init]) => url === '/api/jobs/j1' && (init?.method ?? 'GET') === 'GET'
  ).length
}

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('useJobDetail polling tiers (#965)', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    cleanup()
  })

  it('awaiting_approval 30s 一拉；审批后立即刷新并恢复 5s', async () => {
    currentDetail = {
      status: 'awaiting_approval',
      nodeStatus: 'awaiting_approval',
    }
    const fetchMock = createFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useJobDetail('ws1', 'j1'), { wrapper })
    await waitFor(() => expect(result.current.detail).not.toBeNull())
    expect(detailGets(fetchMock)).toBe(1)

    // 5s 档位已关闭：待审批期间不再每 5s 拉一次。
    await advance(5_000)
    expect(detailGets(fetchMock)).toBe(1)
    // 30s 档：听旁路会话的审批。
    await advance(25_000)
    expect(detailGets(fetchMock)).toBe(2)

    await act(() => result.current.handleApproval('gate', 'approved', '', ''))
    // 审批成功后立即 refetch，不等下一个 30s 周期。
    expect(detailGets(fetchMock)).toBe(3)
    expect(result.current.detail?.job.status).toBe('running')

    await advance(5_000)
    expect(detailGets(fetchMock)).toBe(4)
  })

  it('终态停轮询；重跑后恢复 5s', async () => {
    currentDetail = { status: 'completed', nodeStatus: 'completed' }
    const fetchMock = createFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useJobDetail('ws1', 'j1'), { wrapper })
    await waitFor(() => expect(result.current.detail).not.toBeNull())

    await advance(60_000)
    expect(detailGets(fetchMock)).toBe(1)

    await act(() => result.current.handleRerun('gate'))
    expect(detailGets(fetchMock)).toBe(2)
    expect(result.current.detail?.job.status).toBe('queued')

    await advance(5_000)
    expect(detailGets(fetchMock)).toBe(3)
  })
})
