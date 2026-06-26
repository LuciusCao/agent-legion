import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LaTeXSpan } from './LaTeXSpan'
import { expectConsoleWarning } from '../test-setup'

describe('LaTeXSpan', () => {
  it('renders valid latex as katex html', () => {
    render(<LaTeXSpan latex="x^2" />)
    const span = screen.getByTestId('latex-span')
    expect(span.innerHTML).toContain('katex')
  })

  it('renders valid LaTeX', () => {
    render(<LaTeXSpan latex="x = \\frac{1}{2}" />)
    expect(document.querySelector('.katex')).toBeInTheDocument()
  })

  it('falls back to raw text on invalid latex', () => {
    expectConsoleWarning(/Failed to render LaTeX/)
    render(<LaTeXSpan latex="\broken{" />)
    expect(screen.getByTestId('latex-span').textContent).toBe('\\broken{')
  })

  it('falls back to raw text on malformed LaTeX', () => {
    expectConsoleWarning(/Failed to render LaTeX/)
    const brokenLatex = '\\frac{1'
    render(<LaTeXSpan latex={brokenLatex} />)
    expect(screen.getByTestId('latex-span').textContent).toContain('\\frac{1')
  })

  it('renders empty string', () => {
    render(<LaTeXSpan latex="" />)
    expect(screen.getByTestId('latex-span').textContent).toBe('')
  })
})
