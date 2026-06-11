import { describe, it, expect, beforeEach } from 'vitest'
import { useUiStore } from './uiStore'

describe('uiStore', () => {
  beforeEach(() => {
    useUiStore.setState({
      agents: [],
      addDialogOpen: false,
      addContentType: 'knowledge',
      rerunDialogOpen: false,
      toast: null,
      detailPageActions: null,
    })
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
