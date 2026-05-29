import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { SubtitlePanel } from './SubtitlePanel'

describe('SubtitlePanel', () => {
  it('renders subtitle list', () => {
    const { container } = render(
      <SubtitlePanel currentTime={0} onSeek={() => {}} />
    )
    expect(container.querySelector('md-list')).toBeInTheDocument()
  })
})
