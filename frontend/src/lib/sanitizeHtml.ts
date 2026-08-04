const ALLOWED_TAGS = new Set('P BR STRONG EM UL OL LI SPAN DIV IMG'.split(' '))

const ALLOWED_ATTRIBUTES: Record<string, Set<string>> = {
  IMG: new Set(['src', 'alt']),
}

const ALLOWED_SCHEMES = new Set(['http:', 'https:'])

function isSafeSrc(value: string): boolean {
  try {
    return ALLOWED_SCHEMES.has(new URL(value).protocol)
  } catch {
    return false
  }
}

export function sanitizeHtml(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  Array.from(doc.body.querySelectorAll('*')).forEach((el) => {
    if (!ALLOWED_TAGS.has(el.tagName)) {
      const parent = el.parentNode
      if (!parent) return
      while (el.firstChild) parent.insertBefore(el.firstChild, el)
      parent.removeChild(el)
    }
  })
  Array.from(doc.body.querySelectorAll('*')).forEach((el) => {
    const allowedAttrs = ALLOWED_ATTRIBUTES[el.tagName]
    Array.from(el.attributes).forEach((attr) => {
      const keep =
        allowedAttrs?.has(attr.name) &&
        !(el.tagName === 'IMG' && attr.name === 'src' && !isSafeSrc(attr.value))
      if (!keep) el.removeAttribute(attr.name)
    })
  })
  // Avoid hotlink protection blocking images via the Referer header.
  doc.body
    .querySelectorAll('img')
    .forEach((el) => el.setAttribute('referrerpolicy', 'no-referrer'))
  return doc.body.innerHTML
}
