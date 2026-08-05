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

describe('pageHeaderStore', () => {
  it('defaults page header state to null', () => {
    expect(useUiStore.getState().pageTitle).toBe(null)
    expect(useUiStore.getState().pageSubtitle).toBe(null)
    expect(useUiStore.getState().detailPageActions).toBe(null)
  })

  it('sets page title and subtitle', () => {
    useUiStore.getState().setPageTitle('Title')
    useUiStore.getState().setPageSubtitle('Subtitle')
    expect(useUiStore.getState().pageTitle).toBe('Title')
    expect(useUiStore.getState().pageSubtitle).toBe('Subtitle')
  })

  it('sets detail page actions', () => {
    useUiStore.getState().setDetailPageActions('action')
    expect(useUiStore.getState().detailPageActions).not.toBe(null)
    useUiStore.getState().setDetailPageActions(null)
    expect(useUiStore.getState().detailPageActions).toBe(null)
  })
})
