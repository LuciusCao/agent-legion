const ALLOWED_TAGS = new Set('P BR STRONG EM UL OL LI SPAN DIV IMG'.split(' '))

const ALLOWED_ATTRIBUTES: Record<string, Set<string>> = {
  IMG: new Set(['src', 'alt']),
}

export function sanitizeHtml(html: string): string {
  const parser = new DOMParser()
  const doc = parser.parseFromString(html, 'text/html')
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT)
  const toRemove: Element[] = []
  while (walker.nextNode()) {
    const el = walker.currentNode as Element
    if (!ALLOWED_TAGS.has(el.tagName)) toRemove.push(el)
  }
  toRemove.forEach((el) => {
    const parent = el.parentNode
    if (!parent) return
    while (el.firstChild) parent.insertBefore(el.firstChild, el)
    parent.removeChild(el)
  })
  const attrWalker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT)
  while (attrWalker.nextNode()) {
    const el = attrWalker.currentNode as Element
    if (!ALLOWED_TAGS.has(el.tagName)) continue
    const allowedAttrs = ALLOWED_ATTRIBUTES[el.tagName]
    if (allowedAttrs) {
      for (const attr of Array.from(el.attributes)) {
        if (!allowedAttrs.has(attr.name)) el.removeAttribute(attr.name)
      }
    } else {
      while (el.attributes.length > 0) el.removeAttribute(el.attributes[0].name)
    }
  }
  return doc.body.innerHTML
}
