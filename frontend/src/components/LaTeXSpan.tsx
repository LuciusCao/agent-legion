import katex from 'katex'
import { useMemo } from 'react'

interface LaTeXSpanProps {
  latex: string
}

export function LaTeXSpan({ latex }: LaTeXSpanProps) {
  const html = useMemo(() => {
    if (!latex) return ''
    try {
      return katex.renderToString(latex, {
        throwOnError: true,
        displayMode: false,
      })
    } catch {
      return null
    }
  }, [latex])

  if (html === null) {
    return <span data-testid="latex-span">{latex}</span>
  }

  return (
    <span data-testid="latex-span" dangerouslySetInnerHTML={{ __html: html }} />
  )
}
