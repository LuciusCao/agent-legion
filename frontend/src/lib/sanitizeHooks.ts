import DOMPurify from 'dompurify'

// Only absolute http(s) URLs survive; relative and other schemes are dropped.
export const SAFE_URI = /^https?:\/\//i

let hookInstalled = false

// DOMPurify hooks are global, so registration is deferred to the first
// sanitize call (no import-time side effect) and guarded to stay idempotent.
export function ensureSanitizeHooks(): void {
  if (hookInstalled) return
  hookInstalled = true
  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    // Markdown links open in a new tab so a click never navigates the Studio
    // SPA away from its state. href-less links (relative/anchor/unsafe scheme
    // dropped by the URI regexp) are inert; CSS renders them as plain text.
    if (node.tagName === 'A') {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer')
      return
    }
    if (node.tagName !== 'IMG') return
    // DOMPurify always allows data: URIs on img (ADD_DATA_URI_TAGS only extends
    // the built-in set), so re-check src here and drop unsafe values.
    const src = node.getAttribute('src')
    if (src !== null && !SAFE_URI.test(src)) {
      node.removeAttribute('src')
    }
    // Avoid hotlink protection blocking images via the Referer header.
    node.setAttribute('referrerpolicy', 'no-referrer')
  })
}
