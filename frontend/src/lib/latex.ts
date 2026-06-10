export function decodeHtmlEntities(raw: string): string {
  const textarea = document.createElement('textarea')
  textarea.innerHTML = raw
  return textarea.value
}

export interface LatexPart {
  type: 'text' | 'latex'
  content: string
  display: boolean
}

export function extractLatexParts(text: string): LatexPart[] {
  if (!text) return []

  const parts: LatexPart[] = []
  const regex =
    /(\$\$[\s\S]*?\$\$)|(\$[^\$\r\n]*?\$)|(\\\[[\s\S]*?\\\])|(\\\([\s\S]*?\\\))/g

  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(text)) !== null) {
    const start = match.index
    if (start > lastIndex) {
      parts.push({
        type: 'text',
        content: text.slice(lastIndex, start),
        display: false,
      })
    }

    const raw = match[0]
    let content: string
    let display: boolean

    if (raw.startsWith('$$') && raw.endsWith('$$')) {
      content = raw.slice(2, -2)
      display = false
    } else if (raw.startsWith('$') && raw.endsWith('$')) {
      content = raw.slice(1, -1)
      display = false
    } else if (raw.startsWith('\\[') && raw.endsWith('\\]')) {
      content = raw.slice(2, -2)
      display = false
    } else if (raw.startsWith('\\(') && raw.endsWith('\\)')) {
      content = raw.slice(2, -2)
      display = false
    } else {
      content = raw
      display = false
    }

    parts.push({ type: 'latex', content: content.trim(), display })
    lastIndex = regex.lastIndex
  }

  if (lastIndex < text.length) {
    parts.push({
      type: 'text',
      content: text.slice(lastIndex),
      display: false,
    })
  }

  const collapsed: LatexPart[] = []
  for (const part of parts) {
    if (part.type === 'text' && collapsed.length > 0 && collapsed[collapsed.length - 1].type === 'text') {
      collapsed[collapsed.length - 1].content += part.content
    } else {
      collapsed.push(part)
    }
  }

  if (
    collapsed.length >= 2 &&
    collapsed[collapsed.length - 1].type === 'text' &&
    collapsed[collapsed.length - 1].content === '' &&
    collapsed[collapsed.length - 2].type === 'latex'
  ) {
    collapsed.pop()
  }

  return collapsed
}
