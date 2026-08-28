import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useWorkspacePreviewConfig } from './useWorkspacePreviewConfig'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import type { WorkspaceSettingsSnapshot } from './useWorkspaceSettingsQuery'
import { useUiStore } from '../stores/uiStore'

const mockUpdate = vi.fn()

// 组件直连 ../api/workspacePreviewApi（API 拆分后不经 barrel），mock 打同模块。
vi.mock('../api/workspacePreviewApi', async (importOriginal) => {
  const mod =
    await importOriginal<typeof import('../api/workspacePreviewApi')>()
  return {
    ...mod,
    updateWorkspacePreviewHidden: (...args: unknown[]) => mockUpdate(...args),
  }
})

function makeSnapshot(previewHidden: string[]): WorkspaceSettingsSnapshot {
  return {
    workspaceName: 'WS',
    workspaceDescription: '',
    settings: {
      entityType: 'question',
      intakeModes: [],
      labelOverrides: {},
      workflowKey: 'wf',
      previewHidden,
    },
    executionConfiguration: {
      node_limits: [],
      migration_warnings: [],
      agent_capacity: null,
    },
    agentRoutes: [],
  }
}

// 每用例自建 client 并预置快照（TestQueryProvider 的 client 无法从外部注入）。
function renderWithCache(previewHidden: string[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  client.setQueryData(
    extraQueryKeys.workspaceSettings('ws1'),
    makeSnapshot(previewHidden)
  )
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return {
    client,
    ...renderHook(() => useWorkspacePreviewConfig('ws1'), { wrapper }),
  }
}

describe('useWorkspacePreviewConfig', () => {
  beforeEach(() => {
    mockUpdate.mockReset()
    vi.clearAllMocks()
  })

  it('读取快照里的 previewHidden', () => {
    const { result } = renderWithCache(['questions.json'])

    expect(result.current.previewHidden).toEqual(['questions.json'])
  })

  it('取消勾选（隐藏）乐观更新缓存并在成功后保持', async () => {
    mockUpdate.mockResolvedValue({})
    const { result, client } = renderWithCache(['questions.json'])

    await act(async () => {
      await result.current.toggleArtifact('frame.png', false)
    })

    expect(mockUpdate).toHaveBeenCalledWith('ws1', [
      'frame.png',
      'questions.json',
    ])
    const cached = client.getQueryData<WorkspaceSettingsSnapshot>(
      extraQueryKeys.workspaceSettings('ws1')
    )
    expect(cached?.settings.previewHidden).toEqual([
      'frame.png',
      'questions.json',
    ])
  })

  it('勾选（显示）从隐藏列表移除', async () => {
    mockUpdate.mockResolvedValue({})
    const { result, client } = renderWithCache(['questions.json', 'frame.png'])

    await act(async () => {
      await result.current.toggleArtifact('questions.json', true)
    })

    expect(mockUpdate).toHaveBeenCalledWith('ws1', ['frame.png'])
    const cached = client.getQueryData<WorkspaceSettingsSnapshot>(
      extraQueryKeys.workspaceSettings('ws1')
    )
    expect(cached?.settings.previewHidden).toEqual(['frame.png'])
  })

  it('保存失败回滚缓存并 toast', async () => {
    mockUpdate.mockRejectedValue(new Error('boom'))
    const showToast = vi.fn()
    const original = useUiStore.getState().showToast
    act(() => {
      useUiStore.setState({ showToast })
    })
    const { result, client } = renderWithCache(['questions.json'])

    await act(async () => {
      await result.current.toggleArtifact('frame.png', false)
    })

    const cached = client.getQueryData<WorkspaceSettingsSnapshot>(
      extraQueryKeys.workspaceSettings('ws1')
    )
    expect(cached?.settings.previewHidden).toEqual(['questions.json'])
    expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('boom'),
      'error'
    )
    act(() => {
      useUiStore.setState({ showToast: original })
    })
  })
})
