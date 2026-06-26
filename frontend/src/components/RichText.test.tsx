import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RichText } from './RichText'

describe('RichText', () => {
  it('inline mode strips HTML tags and renders plain text', () => {
    render(<RichText mode="inline">{'<p>hello</p>'}</RichText>)
    expect(screen.getByText('hello')).toBeInTheDocument()
    expect(screen.queryByText('<p>')).not.toBeInTheDocument()
  })

  it('inline mode renders LaTeX after stripping HTML', () => {
    render(<RichText mode="inline">{'<p>\\(5200\\)</p>'}</RichText>)
    expect(document.querySelector('.katex')).toBeInTheDocument()
    expect(screen.queryByText('<p>')).not.toBeInTheDocument()
  })

  it('block mode preserves HTML structure and renders LaTeX', () => {
    render(<RichText mode="block">{'<p>面积 $S=\\pi r^2$ 公式</p>'}</RichText>)
    expect(document.querySelector('.katex')).toBeInTheDocument()
    expect(document.querySelector('p')).toBeInTheDocument()
  })

  it('block mode sanitizes dangerous HTML', () => {
    render(
      <RichText mode="block">
        {'<p>hello</p><script>alert(1)</script>'}
      </RichText>
    )
    expect(screen.getByText('hello')).toBeInTheDocument()
    expect(document.querySelector('script')).not.toBeInTheDocument()
  })

  it('treats display delimiters as inline', () => {
    render(<RichText mode="inline">{'$$\\sum_{i=1}^n i$$'}</RichText>)
    const spans = document.querySelectorAll('.katex')
    expect(spans.length).toBe(1)
  })

  it('returns empty span for empty string', () => {
    render(<RichText mode="inline">{''}</RichText>)
    expect(screen.getByTestId('rich-text').textContent).toBe('')
  })
})
