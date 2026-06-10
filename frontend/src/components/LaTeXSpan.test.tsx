import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LaTeXSpan } from './LaTeXSpan'

describe('LaTeXSpan', () => {
  it('renders valid latex as katex html', () => {
    render(<LaTeXSpan latex="x^2" />)
    const span = screen.getByTestId('latex-span')
    expect(span.innerHTML).toContain('katex')
  })

  it('falls back to raw text on invalid latex', () => {
    render(<LaTeXSpan latex="\broken{" />)
    expect(screen.getByTestId('latex-span').textContent).toBe('\\broken{')
  })

  it('renders empty string', () => {
    render(<LaTeXSpan latex="" />)
    expect(screen.getByTestId('latex-span').textContent).toBe('')
  })
})
