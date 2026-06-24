import { describe, it, expect } from 'vitest'
import { getSelectedValue } from './materialWeb'

describe('getSelectedValue', () => {
  it('returns value from custom event detail', () => {
    const event = new CustomEvent('change', {
      detail: { value: 'custom-value' },
    })
    expect(getSelectedValue(event)).toBe('custom-value')
  })

  it('falls back to target value for native events', () => {
    const select = document.createElement('select')
    const option = document.createElement('option')
    option.value = 'native-value'
    option.selected = true
    select.appendChild(option)

    const event = new Event('change', { bubbles: true })
    select.dispatchEvent(event)

    expect(getSelectedValue(event)).toBe('native-value')
  })

  it('returns empty string when neither detail nor target is available', () => {
    const event = new Event('change')
    expect(getSelectedValue(event)).toBe('')
  })
})
