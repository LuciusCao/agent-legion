import { describe, it, expect } from 'vitest'
import { decodeHtmlEntities } from './latex'

describe('decodeHtmlEntities', () => {
  it('decodes basic entities', () => {
    expect(decodeHtmlEntities('&lt;')).toBe('<')
    expect(decodeHtmlEntities('&gt;')).toBe('>')
    expect(decodeHtmlEntities('&amp;')).toBe('&')
    expect(decodeHtmlEntities('&quot;')).toBe('"')
  })

  it('decodes numeric entities', () => {
    expect(decodeHtmlEntities('&#39;')).toBe("'")
    expect(decodeHtmlEntities('&#x3C;')).toBe('<')
    expect(decodeHtmlEntities('&#x3E;')).toBe('>')
  })

  it('decodes nbsp', () => {
    expect(decodeHtmlEntities('&nbsp;')).toBe('\u00A0')
  })

  it('leaves plain text unchanged', () => {
    expect(decodeHtmlEntities('hello world')).toBe('hello world')
  })

  it('decodes mixed content', () => {
    expect(decodeHtmlEntities('a &lt; b &amp; c')).toBe('a < b & c')
  })
})
