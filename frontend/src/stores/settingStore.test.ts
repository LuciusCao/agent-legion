import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useSettingStore } from './settingStore'
import { useUiStore } from './uiStore'
import { api } from '../api'

vi.mock('../api', () => ({ api: vi.fn() }))

vi.mock('./uiStore', () => ({
  useUiStore: {
    getState: vi.fn(),
    setState: vi.fn(),
  },
}))

const mockApi = vi.mocked(api)
const mockShowToast = vi.fn()
const mockGetState = vi.mocked(useUiStore.getState)

const defaultState = {
  workspaceId: 'ws1',
  settings: {
    cmsUrl: '',
    cmsToken: '',
    entityType: 'question' as const,
    intakeModes: [],
    labelOverrides: {},
    pipelineKey: '',
    agentIds: [],
    concurrencyLimit: 1,
  },
  testStatus: { state: 'idle' as const },
  isSaving: false,
  saveError: null as string | null,
}

describe('settingStore', () => {
  beforeEach(() => {
    useSettingStore.setState(defaultState)
    mockApi.mockReset()
    mockShowToast.mockReset()
    mockGetState.mockReturnValue({ showToast: mockShowToast })
  })

  it('updates settings via setSettings', () => {
    useSettingStore
      .getState()
      .setSettings({ cmsUrl: 'https://cms.example.com' })
    expect(useSettingStore.getState().settings.cmsUrl).toBe(
      'https://cms.example.com'
    )
  })

  it('updates labelOverrides via setSettings', () => {
    useSettingStore.getState().setSettings({ labelOverrides: { a: 'B' } })
    expect(useSettingStore.getState().settings.labelOverrides).toEqual({
      a: 'B',
    })
  })

  it('cycles through testConnection states', async () => {
    mockApi.mockResolvedValueOnce({ ok: true, message: 'connected' })
    const promise = useSettingStore.getState().testConnection()
    expect(useSettingStore.getState().testStatus.state).toBe('testing')
    await promise
    expect(useSettingStore.getState().testStatus.state).toBe('success')
    expect(useSettingStore.getState().testStatus.message).toBe('connected')
    expect(mockShowToast).toHaveBeenCalledWith('连接成功', 'success')
  })

  it('sets failed on testConnection error and shows toast', async () => {
    mockApi.mockRejectedValueOnce(new Error('network error'))
    await useSettingStore.getState().testConnection()
    expect(useSettingStore.getState().testStatus.state).toBe('failed')
    expect(useSettingStore.getState().testStatus.message).toBe('network error')
    expect(mockShowToast).toHaveBeenCalledWith(
      '连接测试失败：network error',
      'error'
    )
  })

  it('saveSection calls PATCH endpoint and shows success toast', async () => {
    mockApi.mockResolvedValueOnce(undefined)
    await useSettingStore
      .getState()
      .saveSection('connection', { cmsUrl: 'http://x' })
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/settings/connection',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ cmsUrl: 'http://x' }),
      })
    )
    expect(mockShowToast).toHaveBeenCalledWith('设置已保存', 'success')
  })

  it('saveSection surfaces 404 errors', async () => {
    const err = Object.assign(new Error('Not Found'), { status: 404 })
    mockApi.mockRejectedValueOnce(err)
    await expect(
      useSettingStore.getState().saveSection('pipeline', { pipelineKey: 'v' })
    ).resolves.toBeUndefined()
    expect(useSettingStore.getState().isSaving).toBe(false)
    expect(useSettingStore.getState().saveError).toBe('Not Found')
    expect(mockShowToast).toHaveBeenCalledWith('Not Found', 'error')
  })

  it('saveSection sets saveError and shows error toast on failure', async () => {
    const err = Object.assign(new Error('Server Error'), { status: 500 })
    mockApi.mockRejectedValueOnce(err)
    await useSettingStore
      .getState()
      .saveSection('connection', { cmsUrl: 'http://x' })
    expect(useSettingStore.getState().saveError).toBe('Server Error')
    expect(useSettingStore.getState().isSaving).toBe(false)
    expect(mockShowToast).toHaveBeenCalledWith('Server Error', 'error')
  })

  it('fetchSettings hydrates settings from API', async () => {
    mockApi.mockResolvedValueOnce({
      cmsUrl: 'https://cms.example.com',
      cmsToken: 'token',
      entityType: 'knowledge',
      intakeModes: ['direct_ids'],
      labelOverrides: { direct_ids: '输入 ID' },
      pipelineKey: 'knowledge_content',
    })
    await useSettingStore.getState().fetchSettings('ws1')
    const { settings } = useSettingStore.getState()
    expect(settings.cmsUrl).toBe('https://cms.example.com')
    expect(settings.cmsToken).toBe('token')
    expect(settings.entityType).toBe('knowledge')
    expect(settings.intakeModes).toEqual(['direct_ids'])
    expect(settings.labelOverrides).toEqual({ direct_ids: '输入 ID' })
    expect(settings.pipelineKey).toBe('knowledge_content')
  })

  it('fetchSettings keeps defaults on 404', async () => {
    const err = Object.assign(new Error('Not Found'), { status: 404 })
    mockApi.mockRejectedValueOnce(err)
    await useSettingStore.getState().fetchSettings('ws1')
    expect(useSettingStore.getState().settings).toEqual(defaultState.settings)
    expect(useSettingStore.getState().saveError).toBeNull()
  })

  it('fetchSettings keeps defaults on empty response', async () => {
    mockApi.mockResolvedValueOnce({})
    await useSettingStore.getState().fetchSettings('ws1')
    expect(useSettingStore.getState().settings).toEqual(defaultState.settings)
  })
})
