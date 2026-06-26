import {
  extractLatexParts,
  renderLatexInHtml,
  sanitizeLatex,
} from '../lib/latex'
import { sanitizeHtml } from '../lib/sanitizeHtml'
import { stripHtmlTags } from '../lib/htmlText'
import { LaTeXSpan } from './LaTeXSpan'

export interface RichTextProps {
  children: string
  mode?: 'inline' | 'block'
}

export function RichText({ children, mode = 'inline' }: RichTextProps) {
  if (mode === 'block') {
    const html = renderLatexInHtml(sanitizeHtml(children))
    return <span dangerouslySetInnerHTML={{ __html: html }} />
  }

  const text = stripHtmlTags(children)
  const parts = extractLatexParts(sanitizeLatex(text))

  return (
    <span data-testid="rich-text">
      {parts.map((part, i) =>
        part.type === 'latex' ? (
          <LaTeXSpan key={i} latex={part.content} />
        ) : (
          <span key={i}>{part.content}</span>
        )
      )}
    </span>
  )
}
