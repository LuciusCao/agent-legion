import { describe, it, expect } from 'vitest'
import {
  extractPlainText,
  adjustHighlightBoundaries,
  buildHighlightedStemHtml,
} from './questionHighlight'
import type { KeyInfoItem } from '../types'

function makeKeyInfo(start: number, end: number): KeyInfoItem {
  return {
    key_info_id: `ki_${start}_${end}`,
    type: 'given',
    content: {
      text: 'placeholder',
      position: { start, end },
    },
    question: { text: '', options: [] },
    question_comprehension_abilities: [],
  }
}

function makeKeyInfoWithText(
  text: string,
  start: number,
  end: number
): KeyInfoItem {
  return {
    key_info_id: `ki_${text}`,
    type: 'given',
    content: { text, position: { start, end } },
    question: { text: '', options: [] },
    question_comprehension_abilities: [],
  }
}

describe('extractPlainText', () => {
  it('strips html tags', () => {
    expect(extractPlainText('<p>hello <strong>world</strong></p>')).toBe(
      'hello world'
    )
  })

  it('decodes common entities', () => {
    expect(extractPlainText('a &lt; b &amp; c')).toBe('a < b & c')
  })

  it('preserves latex delimiters', () => {
    expect(extractPlainText('$x^2$')).toBe('$x^2$')
  })
})

describe('adjustHighlightBoundaries', () => {
  it('expands end boundary inside inline latex formula', () => {
    const text = '已知 $f(x)=x^2$ 求值'
    expect(adjustHighlightBoundaries(text, 2, 7)).toEqual({ start: 2, end: 13 })
  })

  it('expands start boundary inside inline latex formula', () => {
    const text = '已知 $f(x)=x^2$ 求值'
    expect(adjustHighlightBoundaries(text, 5, 14)).toEqual({
      start: 3,
      end: 14,
    })
  })

  it('expands boundary to include full display formula', () => {
    const text = '计算 \\[x^2+1\\] 结果'
    expect(adjustHighlightBoundaries(text, 4, 6)).toEqual({ start: 3, end: 12 })
  })

  it('clamps out of range indices', () => {
    const text = 'short'
    expect(adjustHighlightBoundaries(text, -1, 100)).toEqual({
      start: 0,
      end: 5,
    })
  })
})

describe('buildHighlightedStemHtml', () => {
  it('returns null when no items', () => {
    const stem = '<p>hello</p>'
    expect(buildHighlightedStemHtml(stem, [])).toBeNull()
  })

  it('wraps single range with highlight span', () => {
    const stem = '若关于 x 的方程'
    const html = buildHighlightedStemHtml(stem, [makeKeyInfo(2, 7)])
    expect(html).toContain(
      '<span class="highlight" data-ids="ki_2_7">于 x 的</span>'
    )
  })

  it('merges overlapping ranges', () => {
    const stem = 'abcdef'
    const html = buildHighlightedStemHtml(stem, [
      makeKeyInfo(1, 3),
      makeKeyInfo(2, 5),
    ])
    expect(html).toBe(
      'a<span class="highlight" data-ids="ki_1_3,ki_2_5">bcde</span>f'
    )
  })

  it('ignores invalid positions', () => {
    const stem = 'abc'
    const html = buildHighlightedStemHtml(stem, [makeKeyInfo(5, 10)])
    expect(html).toBeNull()
  })

  it('escapes html in plain text segments', () => {
    const stem = 'a < b & c'
    const html = buildHighlightedStemHtml(stem, [makeKeyInfo(0, 1)])
    expect(html).toBe(
      '<span class="highlight" data-ids="ki_0_1">a</span> &lt; b &amp; c'
    )
  })

  it('falls back to content.text when position is mismatched', () => {
    const stem = '修路队要修一条公路，每月可修276千米，修了17月后还剩69千米。'
    const html = buildHighlightedStemHtml(stem, [
      makeKeyInfoWithText('每月可修276千米', 13, 22),
    ])
    expect(html).toContain(
      '<span class="highlight-corrected" data-ids="ki_每月可修276千米">每月可修276千米</span>'
    )
  })

  it('picks the nearest occurrence for ambiguous target text', () => {
    const stem = 'abc abc abc'
    const html = buildHighlightedStemHtml(stem, [
      makeKeyInfoWithText('abc', 9, 11),
    ])
    expect(html).toBe(
      'abc abc <span class="highlight-corrected" data-ids="ki_abc">abc</span>'
    )
  })

  it('keeps original position when target text is not found', () => {
    const stem = 'abcdef'
    const html = buildHighlightedStemHtml(stem, [makeKeyInfo(1, 3)])
    expect(html).toBe('a<span class="highlight" data-ids="ki_1_3">bc</span>def')
  })

  it('uses normal highlight class when position matches content.text', () => {
    const stem = '修路队要修一条公路，每月可修276千米，修了17月后还剩69千米。'
    const html = buildHighlightedStemHtml(stem, [
      makeKeyInfoWithText('每月可修276千米', 10, 19),
    ])
    expect(html).toContain(
      '<span class="highlight" data-ids="ki_每月可修276千米">每月可修276千米</span>'
    )
  })
})
