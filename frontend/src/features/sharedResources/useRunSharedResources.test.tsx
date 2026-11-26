import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { fetchSharedResourceRunConfirmation } from '../../api/sharedResources'
import type { SharedResourceRunConfirmationResponse } from './sharedResourceTypes'
import { useRunSharedResources } from './useRunSharedResources'
import { SharedResourceRunConfirmation } from './SharedResourceRunConfirmation'

vi.mock('../../api/sharedResources', () => ({
  fetchSharedResourceRunConfirmation: vi.fn(),
}))

const wrapper = ({ children }: { children: ReactNode }) => (
  <TestQueryProvider>{children}</TestQueryProvider>
)

function RunConfirmationHarness({ enabled }: { enabled: boolean }) {
  const state = useRunSharedResources('ws1', enabled)
  return (
    <>
      <SharedResourceRunConfirmation state={state} />
      <button disabled={state.isFetching}>创建运行</button>
    </>
  )
}

function confirmation(
  version = 1,
  accessStatus = 'active'
): SharedResourceRunConfirmationResponse {
  return {
    nodes: [{ node_key: 'review', resource_ids: ['resource-1'] }],
    resources: [
      {
        resource_id: 'resource-1',
        name: '通用审核规则',
        version,
        access_status: accessStatus,
      },
    ],
  }
}

describe('useRunSharedResources', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchSharedResourceRunConfirmation).mockResolvedValue(
      confirmation()
    )
  })

  it('创建运行确认读取资源库当前版本', async () => {
    const { result } = renderHook(() => useRunSharedResources('ws1', true), {
      wrapper,
    })
    await waitFor(() => expect(result.current.resources).toHaveLength(1))
    expect(result.current.resources[0]).toMatchObject({
      id: 'resource-1',
      name: '通用审核规则',
      version: 1,
      unavailable: false,
    })
    expect(result.current.hasUnavailable).toBe(false)
  })

  it('撤销授权或无法确认版本时阻止新运行', async () => {
    vi.mocked(fetchSharedResourceRunConfirmation).mockResolvedValue(
      confirmation(1, 'revoked')
    )
    const { result } = renderHook(() => useRunSharedResources('ws1', true), {
      wrapper,
    })
    await waitFor(() => expect(result.current.hasUnavailable).toBe(true))
    expect(result.current.resources[0].unavailable).toBe(true)
  })

  it('缓存 v1 后重开慢重取只显示确认中，落盘 v2 后才允许提交', async () => {
    const v2 = confirmation(2)
    let resolveV2!: (value: SharedResourceRunConfirmationResponse) => void
    vi.mocked(fetchSharedResourceRunConfirmation)
      .mockResolvedValueOnce(confirmation())
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveV2 = resolve
          })
      )
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 60_000 } },
    })
    const view = (enabled: boolean) => (
      <QueryClientProvider client={client}>
        <RunConfirmationHarness enabled={enabled} />
      </QueryClientProvider>
    )
    const { rerender } = render(view(true))
    expect(await screen.findByText('通用审核规则 v1')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '创建运行' })).toBeEnabled()
    )

    rerender(view(false))
    rerender(view(true))
    await waitFor(() =>
      expect(fetchSharedResourceRunConfirmation).toHaveBeenCalledTimes(2)
    )
    expect(screen.getByRole('button', { name: '创建运行' })).toBeDisabled()
    expect(screen.getByText('正在重新确认共享资源…')).toBeInTheDocument()
    expect(screen.queryByText('通用审核规则 v1')).not.toBeInTheDocument()

    await act(async () => {
      resolveV2(v2)
    })
    expect(await screen.findByText('通用审核规则 v2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建运行' })).toBeEnabled()
  })

  it('未声明资源时返回空确认', async () => {
    vi.mocked(fetchSharedResourceRunConfirmation).mockResolvedValue({
      nodes: [],
      resources: [],
    })
    const { result } = renderHook(() => useRunSharedResources('ws1', true), {
      wrapper,
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.hasResources).toBe(false)
    expect(result.current.isError).toBe(false)
  })
})
