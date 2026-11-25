import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { makeSharedResource } from '../../testing/sharedResourceFixtures'
import { fetchActiveWorkflowRevision } from '../../api'
import { listSharedResources } from '../../api/sharedResources'
import { useRunSharedResources } from './useRunSharedResources'

vi.mock('../../api', () => ({ fetchActiveWorkflowRevision: vi.fn() }))
vi.mock('../../api/sharedResources', () => ({ listSharedResources: vi.fn() }))

const wrapper = ({ children }: { children: ReactNode }) => (
  <TestQueryProvider>{children}</TestQueryProvider>
)

describe('useRunSharedResources', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchActiveWorkflowRevision).mockResolvedValue({
      definition_yaml:
        'nodes:\n  review:\n    type: code\n    shared_resources: [resource-1]\n',
    } as never)
    vi.mocked(listSharedResources).mockResolvedValue({
      resources: [makeSharedResource()],
      total: 1,
      limit: 200,
      offset: 0,
    })
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
    vi.mocked(listSharedResources).mockResolvedValue({
      resources: [makeSharedResource({ access_status: 'revoked' })],
      total: 1,
      limit: 200,
      offset: 0,
    })
    const { result } = renderHook(() => useRunSharedResources('ws1', true), {
      wrapper,
    })
    await waitFor(() => expect(result.current.hasUnavailable).toBe(true))
    expect(result.current.resources[0].unavailable).toBe(true)
  })

  it('未声明资源时不请求资源库', async () => {
    vi.mocked(fetchActiveWorkflowRevision).mockResolvedValue({
      definition_yaml: 'nodes:\n  review:\n    type: code\n',
    } as never)
    const { result } = renderHook(() => useRunSharedResources('ws1', true), {
      wrapper,
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.hasResources).toBe(false)
    expect(listSharedResources).not.toHaveBeenCalled()
  })
})
