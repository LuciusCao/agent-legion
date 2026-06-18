import type { KeyInfoItem } from '../types'

const LATEX_DELIMITERS = [
  { open: '$$', close: '$$' },
  { open: '\\[', close: '\\]' },
  { open: '$', close: '$' },
  { open: '\\(', close: '\\)' },
]

export function extractPlainText(html: string): string {
  if (typeof window === 'undefined') {
    return html
      .replace(/<[^>]+>/g, '')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&amp;/g, '&')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&nbsp;/g, '\u00A0')
  }
  const parser = new DOMParser()
  const doc = parser.parseFromString(html, 'text/html')
  return doc.body.textContent || ''
}

interface LatexRange {
  start: number
  end: number
}

function findLatexRanges(text: string): LatexRange[] {
  const ranges: LatexRange[] = []
  for (const { open, close } of LATEX_DELIMITERS) {
    let pos = 0
    while (pos < text.length) {
      const openPos = text.indexOf(open, pos)
      if (openPos === -1) break
      const contentStart = openPos + open.length
      const closePos = text.indexOf(close, contentStart)
      if (closePos === -1) break
      ranges.push({ start: openPos, end: closePos + close.length })
      pos = closePos + close.length
    }
  }
  return ranges.sort((a, b) => a.start - b.start)
}

function expandToLatexBoundary(
  text: string,
  index: number,
  side: 'start' | 'end'
): number {
  const ranges = findLatexRanges(text)
  for (const range of ranges) {
    if (index > range.start && index < range.end) {
      return side === 'start' ? range.start : range.end
    }
  }
  return index
}

export function adjustHighlightBoundaries(
  text: string,
  start: number,
  end: number
): { start: number; end: number } {
  if (start < 0) start = 0
  if (end > text.length) end = text.length
  if (end <= start) return { start, end }

  const newStart = expandToLatexBoundary(text, start, 'start')
  const newEnd = expandToLatexBoundary(text, end, 'end')
  return { start: Math.min(newStart, start), end: Math.max(newEnd, end) }
}

export function buildHighlightedStemHtml(
  stem: string,
  items: KeyInfoItem[]
): string | null {
  const plain = extractPlainText(stem)
  if (!plain || items.length === 0) {
    return null
  }

  const ranges = items
    .map((item) => {
      const pos = item.content.position
      if (
        !pos ||
        typeof pos.start !== 'number' ||
        typeof pos.end !== 'number' ||
        pos.start < 0 ||
        pos.end > plain.length ||
        pos.end <= pos.start
      ) {
        return null
      }
      const bounds = adjustHighlightBoundaries(plain, pos.start, pos.end)
      return { ...bounds, id: item.key_info_id }
    })
    .filter((r): r is { start: number; end: number; id: string } => r !== null)
    .sort((a, b) => a.start - b.start)

  if (ranges.length === 0) return null

  const merged: { start: number; end: number; ids: string[] }[] = []
  for (const { start, end, id } of ranges) {
    const last = merged[merged.length - 1]
    if (last && start <= last.end) {
      last.end = Math.max(last.end, end)
      last.ids.push(id)
    } else {
      merged.push({ start, end, ids: [id] })
    }
  }

  function escapeHtml(s: string): string {
    return s.replace(
      /[&<>"]/g,
      (c) =>
        ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
        })[c] as string
    )
  }

  let html = ''
  let cursor = 0
  for (const { start, end, ids } of merged) {
    if (start > cursor) {
      html += escapeHtml(plain.slice(cursor, start))
    }
    const dataAttr = ids.length
      ? ` data-ids="${escapeHtml(ids.join(','))}"`
      : ''
    html += `<span class="highlight"${dataAttr}>${escapeHtml(plain.slice(start, end))}</span>`
    cursor = end
  }
  if (cursor < plain.length) {
    html += escapeHtml(plain.slice(cursor))
  }
  return html
}
