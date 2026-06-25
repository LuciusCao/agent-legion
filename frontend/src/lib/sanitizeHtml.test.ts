import { describe, it, expect } from 'vitest'
import { sanitizeHtml } from './sanitizeHtml'

describe('sanitizeHtml', () => {
  it('preserves allowed tags', () => {
    expect(sanitizeHtml('<p>hello</p>')).toBe('<p>hello</p>')
  })

  it('removes disallowed tags', () => {
    expect(sanitizeHtml('<iframe>alert(1)</iframe>')).toBe('alert(1)')
  })

  it('strips attributes from allowed tags by default', () => {
    expect(sanitizeHtml('<p class="x">hello</p>')).toBe('<p>hello</p>')
  })

  it('preserves img tags with src and alt', () => {
    const html =
      '<p>Text</p><img src="https://example.com/img.png" alt="diagram">'
    expect(sanitizeHtml(html)).toBe(
      '<p>Text</p><img src="https://example.com/img.png" alt="diagram">'
    )
  })

  it('removes dangerous attributes from img tags', () => {
    const html = '<img src="https://example.com/img.png" onerror="alert(1)">'
    expect(sanitizeHtml(html)).toBe('<img src="https://example.com/img.png">')
  })

  it('removes img src with dangerous schemes', () => {
    expect(sanitizeHtml('<img src="javascript:alert(1)">')).toBe('<img>')
    expect(sanitizeHtml('<img src="data:image/svg+xml,<svg></svg>">')).toBe(
      '<img>'
    )
  })

  it('removes relative img src', () => {
    expect(sanitizeHtml('<img src="image.png">')).toBe('<img>')
  })

  it('preserves absolute http/https img src', () => {
    expect(sanitizeHtml('<img src="https://example.com/img.png">')).toBe(
      '<img src="https://example.com/img.png">'
    )
  })
})
