import DOMPurify from 'dompurify'

// Only absolute http(s) URLs survive; relative and other schemes are dropped.
const SAFE_SRC = /^https?:\/\//i

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName !== 'IMG') return
  // DOMPurify always allows data: URIs on img (ADD_DATA_URI_TAGS only extends
  // the built-in set), so re-check src here and drop unsafe values.
  const src = node.getAttribute('src')
  if (src !== null && !SAFE_SRC.test(src)) {
    node.removeAttribute('src')
  }
  // Avoid hotlink protection blocking images via the Referer header.
  node.setAttribute('referrerpolicy', 'no-referrer')
})

export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: 'p br strong em ul ol li span div img'.split(' '),
    ALLOWED_ATTR: ['src', 'alt', 'referrerpolicy'],
    // Disallowed tags are unwrapped, keeping their child nodes — including
    // raw-text elements like iframe/script, whose text stays inert.
    KEEP_CONTENT: true,
    FORBID_CONTENTS: [],
    ALLOWED_URI_REGEXP: SAFE_SRC,
  })
}
