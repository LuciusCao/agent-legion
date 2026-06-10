import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AppShell, useAppShellScroll } from './AppShell'

describe('AppShell', () => {
  it('passes scrolled=false initially to appBar render prop', () => {
    const appBarFn = vi.fn(() => <div data-testid="bar">Bar</div>)
    render(
      <AppShell appBar={appBarFn}>
        <div data-testid="content">Content</div>
      </AppShell>
    )
    expect(appBarFn).toHaveBeenLastCalledWith({ scrolled: false })
    expect(screen.getByTestId('bar')).toBeInTheDocument()
    expect(screen.getByTestId('content')).toBeInTheDocument()
  })

  it('provides useAppShellScroll context override', () => {
    function Reporter() {
      const { reportScrolled } = useAppShellScroll()
      reportScrolled(true)
      return <div>Reporter</div>
    }
    const appBarFn = vi.fn(() => <div>Bar</div>)
    render(
      <AppShell appBar={appBarFn}>
        <Reporter />
      </AppShell>
    )
    expect(appBarFn).toHaveBeenLastCalledWith({ scrolled: true })
  })
})
