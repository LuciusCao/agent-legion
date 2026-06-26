export function escapeHtml(s: string): string {
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

/**
 * Strip all HTML tags and decode HTML entities, returning plain text.
 * Useful for inline values (e.g. answer badges) where HTML markup should
 * not be rendered.
 */
export function stripHtmlTags(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  return doc.body.textContent || ''
}
