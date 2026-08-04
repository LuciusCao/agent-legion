import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SubtitlePanel } from './SubtitlePanel'

describe('SubtitlePanel', () => {
  it('renders subtitle list', () => {
    render(<SubtitlePanel currentTime={0} onSeek={() => {}} />)
    expect(screen.getByRole('list')).toBeInTheDocument()
  })
})
