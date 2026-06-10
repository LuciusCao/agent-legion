import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LaTeXText } from './LaTeXText'

describe('LaTeXText', () => {
  it('renders plain text without latex', () => {
    render(<LaTeXText>hello world</LaTeXText>)
    expect(screen.getByText('hello world')).toBeInTheDocument()
  })

  it('renders mixed text and latex', () => {
    render(<LaTeXText>面积 $S=\\pi r^2$ 公式</LaTeXText>)
    expect(screen.getByText('面积')).toBeInTheDocument()
    expect(screen.getByText('公式')).toBeInTheDocument()
    expect(screen.getAllByTestId('latex-span').length).toBe(1)
  })

  it('renders html-escaped latex after sanitization', () => {
    render(<LaTeXText>&lt; $x &lt; 5$</LaTeXText>)
    expect(screen.getByTestId('latex-text').textContent).toContain('<')
    expect(screen.getAllByTestId('latex-span').length).toBe(1)
  })

  it('renders bare latex commands wrapped automatically', () => {
    render(<LaTeXText>{'x = \\frac{1}{2}'}</LaTeXText>)
    expect(screen.getByTestId('latex-text').textContent).toContain('x =')
    expect(screen.getAllByTestId('latex-span').length).toBe(1)
  })

  it('downgrades display formula to inline', () => {
    render(<LaTeXText>{'$$\\sum_{i=1}^n i$$'}</LaTeXText>)
    expect(screen.getAllByTestId('latex-span').length).toBe(1)
  })

  it('handles empty string', () => {
    render(<LaTeXText></LaTeXText>)
    const container = screen.getByTestId('latex-text')
    expect(container.textContent).toBe('')
  })
})
