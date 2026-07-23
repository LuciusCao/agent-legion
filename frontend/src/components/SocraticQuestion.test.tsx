import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SocraticQuestion } from './SocraticQuestion'

describe('SocraticQuestion', () => {
  it('renders nothing when question is missing', () => {
    const { container } = render(<SocraticQuestion />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when question has neither text nor options', () => {
    const { container } = render(
      <SocraticQuestion question={{ text: '', options: [] }} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the question text', () => {
    render(
      <SocraticQuestion
        question={{ text: '为什么要先统一单位？', options: [] }}
      />
    )
    expect(screen.getByText('苏格拉底提问')).toBeInTheDocument()
    expect(screen.getByText('为什么要先统一单位？')).toBeInTheDocument()
  })

  it('renders options and marks the correct one', () => {
    render(
      <SocraticQuestion
        question={{
          text: '',
          options: [
            { label: 'A', text: '因为单位不同', is_correct: true },
            { label: '', text: '凭感觉猜', is_correct: false },
          ],
        }}
      />
    )
    expect(screen.getByText('A.')).toBeInTheDocument()
    expect(screen.getByText('因为单位不同')).toBeInTheDocument()
    expect(screen.getByText('✓ 正确')).toBeInTheDocument()
    // Missing label falls back to the option index letter.
    expect(screen.getByText('B.')).toBeInTheDocument()
  })
})
