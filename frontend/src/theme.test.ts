import { describe, it, expect, beforeEach } from 'vitest'
import { applyTheme } from './theme'

describe('applyTheme', () => {
  beforeEach(() => {
    const root = document.documentElement
    root.removeAttribute('style')
  })

  it('sets elevation tokens for floating panels', () => {
    applyTheme()
    const style = getComputedStyle(document.documentElement)
    expect(style.getPropertyValue('--md-sys-elevation-level3').trim()).not.toBe(
      ''
    )
  })

  it('sets surface container tokens so panels are not transparent', () => {
    applyTheme()
    const style = getComputedStyle(document.documentElement)
    expect(
      style.getPropertyValue('--md-sys-color-surface-container').trim()
    ).not.toBe('')
    expect(
      style.getPropertyValue('--md-sys-color-surface-container-low').trim()
    ).not.toBe('')
    expect(
      style.getPropertyValue('--md-sys-color-surface-container-high').trim()
    ).not.toBe('')
    expect(
      style.getPropertyValue('--md-sys-color-surface-container-highest').trim()
    ).not.toBe('')
  })
})
