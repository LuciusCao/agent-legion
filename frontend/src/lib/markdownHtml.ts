import { marked } from 'marked'
import { sanitizeHtml } from './sanitizeHtml'

// Tags/attrs that markdown output needs beyond the base sanitize profile.
// Links only keep http(s) hrefs (sanitizeHtml's URI regexp drops the rest);
// img stays subject to the hooks module's http(s)-only src hook. h5/h6 are
// allowed too — marked emits them for #####/###### and they are plain
// structure. GFM tables are pure structural tags once sanitized.
const MARKDOWN_TAGS = [
  'code',
  'pre',
  'blockquote',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'a',
  'hr',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
]
const MARKDOWN_ATTRS = ['href']

/** Parse markdown to sanitized HTML. breaks: on so single newlines render
 * like the previous pre-wrap plain-text bubbles. */
export function renderMarkdownHtml(markdown: string): string {
  const raw = marked.parse(markdown, { async: false, gfm: true, breaks: true })
  return sanitizeHtml(raw, { tags: MARKDOWN_TAGS, attrs: MARKDOWN_ATTRS })
}
