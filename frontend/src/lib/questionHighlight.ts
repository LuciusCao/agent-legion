import type { KeyInfoItem } from '../types'
import { escapeHtml } from './htmlText'

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

export interface StemPart {
  type: 'plain' | 'highlight'
  text: string
  ids?: string[]
  corrected?: boolean
}

function getItemTargetText(item: KeyInfoItem): string | undefined {
  return item.content.text || item.content.derived_text
}

function findNearestOccurrence(
  plain: string,
  target: string,
  nearStart: number
): number {
  let idx = plain.indexOf(target)
  if (idx === -1) return -1
  let best = idx
  let bestDist = Math.abs(idx - nearStart)
  while (true) {
    idx = plain.indexOf(target, idx + 1)
    if (idx === -1) break
    const dist = Math.abs(idx - nearStart)
    if (dist < bestDist) {
      bestDist = dist
      best = idx
    }
  }
  return best
}

function resolveHighlightBounds(
  plain: string,
  item: KeyInfoItem
): { start: number; end: number; id: string; corrected: boolean } | null {
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

  let start = pos.start
  let end = pos.end
  let corrected = false
  const target = getItemTargetText(item)
  if (target) {
    const actual = plain.slice(start, end)
    if (actual !== target) {
      const found = findNearestOccurrence(plain, target, start)
      if (found !== -1) {
        start = found
        end = found + target.length
        corrected = true
      }
    }
  }

  const bounds = adjustHighlightBoundaries(plain, start, end)
  return { ...bounds, id: item.key_info_id, corrected }
}

function computeMergedRanges(
  plain: string,
  items: KeyInfoItem[]
): { start: number; end: number; ids: string[]; corrected: boolean }[] | null {
  const ranges = items
    .map((item) => resolveHighlightBounds(plain, item))
    .filter(
      (
        r
      ): r is { start: number; end: number; id: string; corrected: boolean } =>
        r !== null
    )
    .sort((a, b) => a.start - b.start)

  if (ranges.length === 0) return null

  const merged: {
    start: number
    end: number
    ids: string[]
    corrected: boolean
  }[] = []
  for (const { start, end, id, corrected } of ranges) {
    const last = merged[merged.length - 1]
    if (last && start <= last.end) {
      last.end = Math.max(last.end, end)
      last.corrected = last.corrected || corrected
      last.ids.push(id)
    } else {
      merged.push({ start, end, ids: [id], corrected })
    }
  }
  return merged
}

export function buildHighlightedStemParts(
  stem: string,
  items: KeyInfoItem[]
): StemPart[] {
  const plain = extractPlainText(stem)
  if (!plain || items.length === 0) {
    return [{ type: 'plain', text: plain || stem }]
  }

  const merged = computeMergedRanges(plain, items)
  if (!merged) return [{ type: 'plain', text: plain }]

  const parts: StemPart[] = []
  let cursor = 0
  for (const { start, end, ids, corrected } of merged) {
    if (start > cursor) {
      parts.push({ type: 'plain', text: plain.slice(cursor, start) })
    }
    parts.push({
      type: 'highlight',
      text: plain.slice(start, end),
      ids,
      corrected,
    })
    cursor = end
  }
  if (cursor < plain.length) {
    parts.push({ type: 'plain', text: plain.slice(cursor) })
  }
  return parts
}

export function buildHighlightedStemHtml(
  stem: string,
  items: KeyInfoItem[]
): string | null {
  const parts = buildHighlightedStemParts(stem, items)
  if (parts.length === 1 && parts[0].type === 'plain') {
    return null
  }

  return parts
    .map((part) => {
      const text = escapeHtml(part.text)
      if (part.type === 'highlight') {
        const dataAttr = part.ids?.length
          ? ` data-ids="${escapeHtml(part.ids.join(','))}"`
          : ''
        const className = part.corrected ? 'highlight-corrected' : 'highlight'
        return `<span class="${className}"${dataAttr}>${text}</span>`
      }
      return text
    })
    .join('')
}
