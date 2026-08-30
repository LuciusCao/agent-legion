import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { WorkspaceSettings } from '../../types'

/**
 * saveAll 的 PUT /configuration 契约：settings 必须是白名单 pick。
 * GET /settings 返回的服务端附加键（nodeConfig/nodeConfigSchemas）
 * 不在 PUT 契约（extra=forbid）里，全量回传会 422。
 * workflowKey 已随 #211 Phase 2 第二批停发（key 与 workspace id 恒等
 * 且不可变，PUT 缺省=沿用已存），快照里带着也不得回传。
 */

const mockApi = vi.fn()

vi.mock('../../api', () => ({
  api: (...args: unknown[]) => mockApi(...args),
}))

vi.mock('../../lib/queryClient', () => ({
  queryClient: { invalidateQueries: vi.fn() },
}))

vi.mock('../../stores/uiStore', () => ({
  useUiStore: { getState: () => ({ showToast: vi.fn() }) },
}))

type SettingsStoreModule = typeof import('./index')

async function setupStore(overrides: Partial<WorkspaceSettings> = {}) {
  const { useSettingStore } = (await import('./index')) as SettingsStoreModule
  const settings: WorkspaceSettings = {
    entityType: 'question',
    workflowKey: 'wf',
    previewHidden: ['questions.json'],
    // 服务端 GET 附加键（真实水合后 draft 会带上）。
    nodeConfig: { some_node: { timeout_seconds: 10 } },
    nodeConfigSchemas: { some_node: { fields: [] } },
    ...overrides,
  } as unknown as WorkspaceSettings
  useSettingStore.setState({
    workspaceId: 'ws1',
    workspaceName: 'WS',
    workspaceDescription: '',
    settings,
    originalSettings: settings,
    originalWorkspaceName: 'WS',
    originalWorkspaceDescription: '',
    executionConfiguration: {
      node_limits: [],
      migration_warnings: [],
      agent_capacity: null,
    },
    originalExecutionConfiguration: {
      node_limits: [],
      migration_warnings: [],
      agent_capacity: null,
    },
    isDirty: true,
    isSaving: false,
    saveError: null,
  })
  return useSettingStore
}

describe('saveActions PUT body', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockApi.mockResolvedValue({
      workspace: { name: 'WS', description: '' },
      settings: {},
      execution_configuration: { node_limits: [], migration_warnings: [] },
      agent_capacity: null,
    })
    vi.resetModules()
  })

  it('settings 只含白名单字段（附加键被过滤，不触发 422）', async () => {
    const store = await setupStore()

    const ok = await store.getState().saveAll()

    expect(ok).toBe(true)
    const body = JSON.parse(mockApi.mock.calls[0][1].body)
    expect(Object.keys(body.settings).sort()).toEqual(
      ['entityType', 'previewHidden'].sort()
    )
    expect(body.settings.previewHidden).toEqual(['questions.json'])
    expect(body.settings.workflowKey).toBeUndefined()
    expect(body.settings.nodeConfig).toBeUndefined()
    expect(body.settings.nodeConfigSchemas).toBeUndefined()
  })

  it('previewHidden 随保存透传', async () => {
    const store = await setupStore({ previewHidden: ['a.json', 'b.json'] })

    await store.getState().saveAll()

    const body = JSON.parse(mockApi.mock.calls[0][1].body)
    expect(body.settings.previewHidden).toEqual(['a.json', 'b.json'])
  })
})
