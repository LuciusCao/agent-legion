import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useNavigate } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { AppBar } from './AppBar'
import styles from './AppBar.module.css'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: vi.fn() }
})

const mockedUseNavigate = vi.mocked(useNavigate)

describe('AppBar', () => {
  beforeEach(() => {
    mockedUseNavigate.mockReturnValue(vi.fn())
  })

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
    expect(bar).toHaveClass(styles.scrolled)
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

  it('navigates home when home button clicked', () => {
    const navigate = vi.fn()
    mockedUseNavigate.mockReturnValue(navigate)
    render(
      <MemoryRouter>
        <AppBar title="Video Hive" home />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByTestId('app-bar-home'))
    expect(navigate).toHaveBeenCalledWith('/')
  })

  it('navigates back when back button clicked', () => {
    const navigate = vi.fn()
    mockedUseNavigate.mockReturnValue(navigate)
    render(
      <MemoryRouter>
        <AppBar title="Settings" backTo="/previous" />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByTestId('app-bar-back'))
    expect(navigate).toHaveBeenCalledWith('/previous')
  })

  it('renders no left button when neither home nor backTo provided', () => {
    render(
      <MemoryRouter>
        <AppBar title="Plain" />
      </MemoryRouter>
    )
    expect(screen.queryByTestId('app-bar-home')).not.toBeInTheDocument()
    expect(screen.queryByTestId('app-bar-back')).not.toBeInTheDocument()
    expect(screen.getByText('Plain')).toBeInTheDocument()
  })
})
