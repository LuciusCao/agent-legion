const ALLOWED_TAGS = new Set([
  'P',
  'BR',
  'STRONG',
  'EM',
  'UL',
  'OL',
  'LI',
  'SPAN',
  'DIV',
])

export function sanitizeHtml(html: string): string {
  const parser = new DOMParser()
  const doc = parser.parseFromString(html, 'text/html')
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT)
  const toRemove: Element[] = []
  while (walker.nextNode()) {
    const el = walker.currentNode as Element
    if (!ALLOWED_TAGS.has(el.tagName)) {
      toRemove.push(el)
    }
  }
  toRemove.forEach((el) => {
    const parent = el.parentNode
    if (!parent) return
    while (el.firstChild) {
      parent.insertBefore(el.firstChild, el)
    }
    parent.removeChild(el)
  })
  const attrWalker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT)
  while (attrWalker.nextNode()) {
    const el = attrWalker.currentNode as Element
    if (ALLOWED_TAGS.has(el.tagName)) {
      while (el.attributes.length > 0) {
        el.removeAttribute(el.attributes[0].name)
      }
    }
  }
  return doc.body.innerHTML
}
