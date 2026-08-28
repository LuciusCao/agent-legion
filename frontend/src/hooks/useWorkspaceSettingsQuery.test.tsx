import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createElement, type ReactNode } from 'react'
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query'
import { renderHook, waitFor, act } from '@testing-library/react'
import { createTestQueryClient } from '../testing/testQueryClient'
import {
  useSettingStoreHydration,
  useWorkspaceSettingsQuery,
} from './useWorkspaceSettingsQuery'
import { useSettingStore } from '../stores/settingStore'
import { api } from '../api'
import { getWorkspaceExecutorConfiguration } from '../api/executorApi'

vi.mock('../api', () => ({
  api: vi.fn(),
}))

vi.mock('../api/executorApi', () => ({
  getWorkspaceExecutorConfiguration: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockGetWorkspaceExecutorConfiguration = vi.mocked(
  getWorkspaceExecutorConfiguration
)

const executorConfig = {
  node_limits: [],
  migration_warnings: ['legacy migration'],
  agent_capacity: null,
}

function mockSnapshotApi(workspaceName = '空间一') {
  mockApi.mockImplementation((path: string) => {
    if (path === '/api/workspaces/ws1') {
      return Promise.resolve({
        workspace: { name: workspaceName, description: '描述一' },
      })
    }
    if (path === '/api/workspaces/ws1/settings') {
      return Promise.resolve({
        entityType: 'knowledge',
        workflowKey: 'knowledge_content',
      })
    }
    if (path === '/api/workspaces/ws1/agent-routes') {
      return Promise.resolve({
        routes: [
          {
            workflow_key: 'knowledge_content',
            node_key: 'review',
            node_label: '审核',
            capability: 'review',
            agent_id: 'reviewer',
            agent_skill: 'review_key_info',
          },
        ],
      })
    }
    return Promise.resolve({})
  })
  mockGetWorkspaceExecutorConfiguration.mockResolvedValue(executorConfig)
}

function makeWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children)
  }
}

function resetStore() {
  useSettingStore.setState({
    workspaceId: null,
    workspaceName: '',
    workspaceDescription: '',
    settings: {
      entityType: 'question',
      workflowKey: '',
    },
    originalWorkspaceName: '',
    originalWorkspaceDescription: '',
    originalSettings: null,
    isDirty: false,
    saveError: null,
    executorConfiguration: {
      node_limits: [],
      migration_warnings: [],
      agent_capacity: null,
    },
    originalExecutorConfiguration: null,
  })
}

describe('useWorkspaceSettingsQuery', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockGetWorkspaceExecutorConfiguration.mockReset()
    resetStore()
  })

  it('assembles the snapshot from the five parallel requests', async () => {
    mockSnapshotApi()
    const client = createTestQueryClient()
    const { result } = renderHook(() => useWorkspaceSettingsQuery('ws1'), {
      wrapper: makeWrapper(client),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const snapshot = result.current.data
    expect(snapshot).not.toBeNull()
    if (!snapshot) return
    expect(snapshot.workspaceName).toBe('空间一')
    expect(snapshot.workspaceDescription).toBe('描述一')
    expect(snapshot.settings.entityType).toBe('knowledge')
    expect(snapshot.settings.workflowKey).toBe('knowledge_content')
    expect(snapshot.executorConfiguration.migration_warnings).toEqual([
      'legacy migration',
    ])
    expect(snapshot.agentRoutes).toHaveLength(1)
  })

  it('returns null silently on 404 without entering an error state', async () => {
    mockApi.mockRejectedValueOnce(
      Object.assign(new Error('Not Found'), { status: 404 })
    )
    mockApi.mockResolvedValue({})
    mockGetWorkspaceExecutorConfiguration.mockResolvedValue({
      node_limits: [],
      migration_warnings: [],
    })
    const client = createTestQueryClient()
    const { result } = renderHook(() => useWorkspaceSettingsQuery('ws1'), {
      wrapper: makeWrapper(client),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('degrades agent-routes failures to an empty route list', async () => {
    mockSnapshotApi()
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/workspaces/ws1/agent-routes') {
        return Promise.reject(new Error('boom'))
      }
      if (path === '/api/workspaces/ws1') {
        return Promise.resolve({ workspace: { name: '空间一' } })
      }
      if (path === '/api/workspaces/ws1/settings') {
        return Promise.resolve({ workflowKey: 'knowledge_content' })
      }
      return Promise.resolve({})
    })
    const client = createTestQueryClient()
    const { result } = renderHook(() => useWorkspaceSettingsQuery('ws1'), {
      wrapper: makeWrapper(client),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.agentRoutes).toEqual([])
  })

  it('surfaces non-404 errors as query error', async () => {
    mockApi.mockRejectedValue(
      Object.assign(new Error('HTTP 500'), { status: 500 })
    )
    mockGetWorkspaceExecutorConfiguration.mockResolvedValue({
      node_limits: [],
      migration_warnings: [],
    })
    const client = createTestQueryClient()
    const { result } = renderHook(() => useWorkspaceSettingsQuery('ws1'), {
      wrapper: makeWrapper(client),
    })

    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.data).toBeUndefined()
  })
})

describe('useSettingStoreHydration', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockGetWorkspaceExecutorConfiguration.mockReset()
    resetStore()
  })

  it('hydrates draft and originals when the snapshot arrives', async () => {
    mockSnapshotApi()
    const client = createTestQueryClient()
    renderHook(() => useSettingStoreHydration('ws1'), {
      wrapper: makeWrapper(client),
    })

    await waitFor(() =>
      expect(useSettingStore.getState().workspaceName).toBe('空间一')
    )
    const state = useSettingStore.getState()
    expect(state.workspaceId).toBe('ws1')
    expect(state.originalWorkspaceName).toBe('空间一')
    expect(state.originalSettings?.workflowKey).toBe('knowledge_content')
    expect(state.isDirty).toBe(false)
  })

  it('resets the draft when switching to another workspace even while dirty', async () => {
    mockSnapshotApi()
    useSettingStore.setState({
      workspaceId: 'ws0',
      workspaceName: '上一个空间的未保存编辑',
      isDirty: true,
    })
    const client = createTestQueryClient()
    renderHook(() => useSettingStoreHydration('ws1'), {
      wrapper: makeWrapper(client),
    })

    await waitFor(() =>
      expect(useSettingStore.getState().workspaceName).toBe('空间一')
    )
    const state = useSettingStore.getState()
    expect(state.workspaceId).toBe('ws1')
    expect(state.isDirty).toBe(false)
  })

  it('does not overwrite the dirty draft when a background refetch arrives', async () => {
    mockSnapshotApi()
    const client = createTestQueryClient()
    renderHook(() => useSettingStoreHydration('ws1'), {
      wrapper: makeWrapper(client),
    })
    await waitFor(() =>
      expect(useSettingStore.getState().workspaceName).toBe('空间一')
    )

    act(() => {
      useSettingStore.getState().setWorkspaceName('编辑中的名字')
    })
    expect(useSettingStore.getState().isDirty).toBe(true)

    mockSnapshotApi('空间一（新）')
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['workspaceSettings'] })
    })

    const state = useSettingStore.getState()
    expect(state.workspaceName).toBe('编辑中的名字')
    expect(state.isDirty).toBe(true)
  })

  it('syncs the refetched snapshot when the store is not dirty', async () => {
    mockSnapshotApi()
    const client = createTestQueryClient()
    renderHook(() => useSettingStoreHydration('ws1'), {
      wrapper: makeWrapper(client),
    })
    await waitFor(() =>
      expect(useSettingStore.getState().workspaceName).toBe('空间一')
    )

    mockSnapshotApi('空间一（新）')
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['workspaceSettings'] })
    })

    await waitFor(() =>
      expect(useSettingStore.getState().workspaceName).toBe('空间一（新）')
    )
    expect(useSettingStore.getState().isDirty).toBe(false)
  })

  it('writes saveError when the snapshot load fails', async () => {
    mockApi.mockRejectedValue(new Error('HTTP 500: Internal Server Error'))
    mockGetWorkspaceExecutorConfiguration.mockResolvedValue({
      node_limits: [],
      migration_warnings: [],
    })
    const client = createTestQueryClient()
    renderHook(() => useSettingStoreHydration('ws1'), {
      wrapper: makeWrapper(client),
    })

    await waitFor(() =>
      expect(useSettingStore.getState().saveError).toBe(
        'HTTP 500: Internal Server Error'
      )
    )
  })
})
