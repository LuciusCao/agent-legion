import { describe, it, expect } from 'vitest'
import { escapeHtml, stripHtmlTags } from './htmlText'

describe('escapeHtml', () => {
  it('escapes ampersands, angle brackets, and quotes', () => {
    expect(escapeHtml('a < b & c > d "e"')).toBe(
      'a &lt; b &amp; c &gt; d &quot;e&quot;'
    )
  })

  it('leaves safe text unchanged', () => {
    expect(escapeHtml('plain text')).toBe('plain text')
  })

  it('escapes HTML tags so they render as text', () => {
    expect(escapeHtml('<script>alert(1)</script>')).toBe(
      '&lt;script&gt;alert(1)&lt;/script&gt;'
    )
  })
})

describe('stripHtmlTags', () => {
  it('removes tags and decodes entities', () => {
    expect(stripHtmlTags('<p>hello &amp; world</p>')).toBe('hello & world')
  })
})
