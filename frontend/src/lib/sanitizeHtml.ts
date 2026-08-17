import DOMPurify from 'dompurify'
import { SAFE_URI, ensureSanitizeHooks } from './sanitizeHooks'

const BASE_TAGS = 'p br strong em ul ol li span div img'.split(' ')

// Markdown rendering (Studio chat agent bubbles) passes extra tags/attrs
// (code/pre/headings/table/a+href); the base profile stays tight for RichText.
export function sanitizeHtml(
  html: string,
  extra?: { tags?: string[]; attrs?: string[] }
): string {
  ensureSanitizeHooks()
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: [...BASE_TAGS, ...(extra?.tags ?? [])],
    ALLOWED_ATTR: ['src', 'alt', 'referrerpolicy', ...(extra?.attrs ?? [])],
    // Disallowed tags are unwrapped, keeping their child nodes — including
    // raw-text elements like iframe/script, whose text stays inert.
    KEEP_CONTENT: true,
    FORBID_CONTENTS: [],
    ALLOWED_URI_REGEXP: SAFE_URI,
  })
}
