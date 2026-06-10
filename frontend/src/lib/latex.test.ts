import { describe, it, expect } from 'vitest'
import { decodeHtmlEntities, extractLatexParts, sanitizeLatex } from './latex'

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

describe('extractLatexParts', () => {
  it('returns plain text as-is', () => {
    expect(extractLatexParts('hello world')).toEqual([
      { type: 'text', content: 'hello world', display: false },
    ])
  })

  it('extracts single inline formula with $', () => {
    expect(extractLatexParts('x = $\\frac{1}{2}$')).toEqual([
      { type: 'text', content: 'x = ', display: false },
      { type: 'latex', content: '\\frac{1}{2}', display: false },
    ])
  })

  it('extracts display formula $$ and downgrades to inline', () => {
    expect(extractLatexParts('$$\\sum_{i=1}^n i$$')).toEqual([
      { type: 'latex', content: '\\sum_{i=1}^n i', display: false },
    ])
  })

  it('extracts display formula \\[...\\] and downgrades to inline', () => {
    expect(extractLatexParts('\\[\\sqrt{2}\\]')).toEqual([
      { type: 'latex', content: '\\sqrt{2}', display: false },
    ])
  })

  it('extracts inline with \\(...\\)', () => {
    expect(extractLatexParts('面积 \\(S=\\pi r^2\\) 公式')).toEqual([
      { type: 'text', content: '面积 ', display: false },
      { type: 'latex', content: 'S=\\pi r^2', display: false },
      { type: 'text', content: ' 公式', display: false },
    ])
  })

  it('handles multiple formulas', () => {
    expect(extractLatexParts('$a$ 和 $b$')).toEqual([
      { type: 'latex', content: 'a', display: false },
      { type: 'text', content: ' 和 ', display: false },
      { type: 'latex', content: 'b', display: false },
    ])
  })

  it('returns empty array for empty string', () => {
    expect(extractLatexParts('')).toEqual([])
  })

  it('handles text with only spaces', () => {
    expect(extractLatexParts('   ')).toEqual([
      { type: 'text', content: '   ', display: false },
    ])
  })
})

describe('sanitizeLatex', () => {
  it('decodes html entities', () => {
    expect(sanitizeLatex('&lt; $x &lt; 5$')).toBe('< $x < 5$')
  })

  it('wraps bare latex commands without delimiters', () => {
    expect(sanitizeLatex('x = \\frac{1}{2}')).toBe('x = $\\frac{1}{2}$')
  })

  it('does not wrap plain text without latex commands', () => {
    expect(sanitizeLatex('hello world')).toBe('hello world')
  })

  it('handles mixed delimited and bare latex', () => {
    expect(sanitizeLatex('面积 $S=\\pi r^2$ 和 \\sqrt{2}')).toBe(
      '面积 $S=\\pi r^2$ 和 $\\sqrt{2}$'
    )
  })

  it('does not double-wrap already delimited latex', () => {
    expect(sanitizeLatex('$\\frac{1}{2}$')).toBe('$\\frac{1}{2}$')
  })

  it('handles html entities in bare latex', () => {
    expect(sanitizeLatex('x &lt; \\frac{1}{2}')).toBe('x < $\\frac{1}{2}$')
  })

  it('does not wrap text that looks like urls', () => {
    expect(sanitizeLatex('visit \\site.com')).toBe('visit \\site.com')
  })

  it('handles empty string', () => {
    expect(sanitizeLatex('')).toBe('')
  })
})
