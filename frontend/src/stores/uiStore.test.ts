import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useUiStore } from './uiStore'

const originalFetch = global.fetch

function mockWorkerStatusFetch(pausedByWorkspace: Record<string, boolean>) {
  return vi.fn().mockImplementation((url: string) => {
    const parsed = new URL(url, 'http://localhost')
    const workspaceId = parsed.searchParams.get('workspace_id') || 'video-hive'
    return Promise.resolve({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () =>
        Promise.resolve({ paused: pausedByWorkspace[workspaceId] ?? true }),
      text: () =>
        Promise.resolve(
          JSON.stringify({ paused: pausedByWorkspace[workspaceId] ?? true })
        ),
    } as Response)
  })
}

describe('uiStore', () => {
  beforeEach(() => {
    useUiStore.setState({
      agents: [],
      addDialogOpen: false,
      addContentType: 'knowledge',
      rerunDialogOpen: false,
      toast: null,
      detailPageActions: null,
      workerPaused: true,
      workerPausedByWorkspace: {},
    })
  })

  afterEach(() => {
    global.fetch = originalFetch
  })

  it('opens and closes add dialog', () => {
    useUiStore.getState().openAddDialog()
    expect(useUiStore.getState().addDialogOpen).toBe(true)
    useUiStore.getState().closeAddDialog()
    expect(useUiStore.getState().addDialogOpen).toBe(false)
  })

  it('shows toast', () => {
    useUiStore.getState().showToast('test', 'success')
    expect(useUiStore.getState().toast).toEqual({
      message: 'test',
      type: 'success',
    })
  })

  it('defaults workerPaused to true to match backend', () => {
    expect(useUiStore.getState().workerPaused).toBe(true)
  })

  it('keeps worker paused state isolated by workspace', async () => {
    global.fetch = mockWorkerStatusFetch({
      ws1: false,
      ws2: true,
    })

    await useUiStore.getState().fetchWorkerStatus('ws1')
    await useUiStore.getState().fetchWorkerStatus('ws2')

    expect(useUiStore.getState().getWorkerPaused('ws1')).toBe(false)
    expect(useUiStore.getState().getWorkerPaused('ws2')).toBe(true)
  })

  it('connectAgentsWs returns a cleanup function', () => {
    const cleanup = useUiStore.getState().connectAgentsWs()
    expect(typeof cleanup).toBe('function')
    expect(() => cleanup()).not.toThrow()
  })

  it('defaults detailPageActions to null', () => {
    expect(useUiStore.getState().detailPageActions).toBe(null)
  })

  it('sets detailPageActions', () => {
    useUiStore.getState().setDetailPageActions('action')
    expect(useUiStore.getState().detailPageActions).not.toBe(null)
    useUiStore.getState().setDetailPageActions(null)
    expect(useUiStore.getState().detailPageActions).toBe(null)
  })
})
