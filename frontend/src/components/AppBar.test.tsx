import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AppBar } from './AppBar'

describe('AppBar', () => {
  it('renders title and home button', () => {
    render(
      <MemoryRouter>
        <AppBar title="Video Hive" home />
      </MemoryRouter>
    )
    expect(screen.getByText('Video Hive')).toBeInTheDocument()
    expect(screen.getByTestId('app-bar-home')).toBeInTheDocument()
  })

  it('prefers backTo over home', () => {
    render(
      <MemoryRouter>
        <AppBar title="Settings" home backTo="/previous" />
      </MemoryRouter>
    )
    expect(screen.getByTestId('app-bar-back')).toBeInTheDocument()
    expect(screen.queryByTestId('app-bar-home')).not.toBeInTheDocument()
  })

  it('applies shadow class when scrolled', () => {
    render(
      <MemoryRouter>
        <AppBar title="Test" scrolled />
      </MemoryRouter>
    )
    const bar = screen.getByTestId('app-bar')
    expect(bar.className).toContain('scrolled')
  })

  it('renders right actions', () => {
    render(
      <MemoryRouter>
        <AppBar
          title="Test"
          rightActions={<div data-testid="action">Action</div>}
        />
      </MemoryRouter>
    )
    expect(screen.getByTestId('action')).toBeInTheDocument()
  })
})
