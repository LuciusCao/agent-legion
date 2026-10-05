import { Marked, type Tokens } from 'marked'

function escapeText(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** Markdown parser whose images render as a click-to-open link placeholder
 * instead of an <img>, so rendering never fetches a resource on its own.
 * A separate instance keeps the global `marked` (artifact previews) on its
 * default image renderer. The href still goes through sanitizeHtml's URI
 * regexp, so only http(s) targets survive as clickable links. */
export const inertImageMarked = new Marked({
  renderer: {
    image({ href, text }: Tokens.Image): string {
      const label = escapeText(text || href)
      return `<a href="${escapeText(href)}">[图片：${label}]</a>`
    },
  },
})
