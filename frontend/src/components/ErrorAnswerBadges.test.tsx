import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ErrorAnswerBadges } from './ErrorAnswerBadges'

describe('ErrorAnswerBadges', () => {
  it('renders plain text answers', () => {
    render(<ErrorAnswerBadges answers={['A', 'B']} />)
    expect(screen.getByText('A')).toBeInTheDocument()
    expect(screen.getByText('B')).toBeInTheDocument()
  })

  it('strips HTML tags and renders LaTeX inline', () => {
    render(<ErrorAnswerBadges answers={['<p>\\(x\\)</p>']} />)
    expect(screen.queryByText('<p>')).not.toBeInTheDocument()
    expect(document.querySelector('.katex')).toBeInTheDocument()
  })
})
