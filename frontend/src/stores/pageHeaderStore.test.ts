import { describe, it, expect } from 'vitest'
import { usePageHeaderStore } from './pageHeaderStore'

describe('pageHeaderStore', () => {
  it('defaults page header state to null', () => {
    expect(usePageHeaderStore.getState().pageTitle).toBe(null)
    expect(usePageHeaderStore.getState().pageSubtitle).toBe(null)
    expect(usePageHeaderStore.getState().detailPageActions).toBe(null)
  })

  it('sets page title and subtitle', () => {
    usePageHeaderStore.getState().setPageTitle('Title')
    usePageHeaderStore.getState().setPageSubtitle('Subtitle')
    expect(usePageHeaderStore.getState().pageTitle).toBe('Title')
    expect(usePageHeaderStore.getState().pageSubtitle).toBe('Subtitle')
  })

  it('sets detail page actions', () => {
    usePageHeaderStore.getState().setDetailPageActions('action')
    expect(usePageHeaderStore.getState().detailPageActions).not.toBe(null)
    usePageHeaderStore.getState().setDetailPageActions(null)
    expect(usePageHeaderStore.getState().detailPageActions).toBe(null)
  })
})
