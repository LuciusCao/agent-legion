import katex from 'katex'
import { useMemo } from 'react'
import { normalizeLatexForKatex } from '../lib/latex'

interface LaTeXSpanProps {
  latex: string
}

export function LaTeXSpan({ latex }: LaTeXSpanProps) {
  const html = useMemo(() => {
    if (!latex) return ''
    try {
      return katex.renderToString(normalizeLatexForKatex(latex), {
        throwOnError: true,
        displayMode: false,
      })
    } catch (err) {
      console.warn('[RichText] Failed to render LaTeX:', latex, err)
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
