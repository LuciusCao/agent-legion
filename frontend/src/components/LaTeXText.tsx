import { sanitizeLatex, extractLatexParts } from '../lib/latex'
import { LaTeXSpan } from './LaTeXSpan'

interface LaTeXTextProps {
  children?: string | string[]
}

export function LaTeXText({ children }: LaTeXTextProps) {
  const text =
    typeof children === 'string'
      ? children
      : Array.isArray(children)
        ? children.join('')
        : ''

  let parts = extractLatexParts(text)

  const hasLatex = parts.some((p) => p.type === 'latex')
  if (!hasLatex) {
    const sanitized = sanitizeLatex(text)
    parts = extractLatexParts(sanitized)
  }

  return (
    <span data-testid="latex-text">
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
