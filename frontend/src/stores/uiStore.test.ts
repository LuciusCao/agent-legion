import { describe, it, expect, beforeEach } from 'vitest'
import { useUiStore } from './uiStore'

describe('uiStore', () => {
  beforeEach(() => {
    useUiStore.setState({
      addDialogOpen: false,
      addContentType: 'knowledge',
      toast: null,
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
})
