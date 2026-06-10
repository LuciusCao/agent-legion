import { describe, it, expect, vi } from 'vitest'
import { render, screen, act, renderHook } from '@testing-library/react'
import { useEffect } from 'react'
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
      useEffect(() => {
        reportScrolled(true)
      }, [reportScrolled])
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

  it('detects native main scroll and passes scrolled=true', () => {
    const appBarFn = vi.fn(() => <div>Bar</div>)
    const { container } = render(
      <AppShell appBar={appBarFn}>
        <div style={{ height: '2000px' }}>Tall content</div>
      </AppShell>
    )
    const main = container.querySelector('main')
    expect(main).toBeTruthy()
    act(() => {
      main!.scrollTop = 10
      main!.dispatchEvent(new Event('scroll', { bubbles: false }))
    })
    expect(appBarFn).toHaveBeenLastCalledWith({ scrolled: true })
  })

  it('falls back to native scroll after resetReportedScroll', () => {
    function Reporter() {
      const { reportScrolled, resetReportedScroll } = useAppShellScroll()
      useEffect(() => {
        reportScrolled(true)
        resetReportedScroll()
      }, [reportScrolled, resetReportedScroll])
      return <div>Reporter</div>
    }
    const appBarFn = vi.fn(() => <div>Bar</div>)
    render(
      <AppShell appBar={appBarFn}>
        <Reporter />
      </AppShell>
    )
    expect(appBarFn).toHaveBeenLastCalledWith({ scrolled: false })
  })

  it('applies mainClassName to main element', () => {
    const appBarFn = vi.fn(() => <div>Bar</div>)
    const { container } = render(
      <AppShell appBar={appBarFn} mainClassName="custom-class">
        <div>Content</div>
      </AppShell>
    )
    const main = container.querySelector('main')
    expect(main?.classList.contains('custom-class')).toBe(true)
  })

  it('throws when useAppShellScroll is used outside AppShell', () => {
    expect(() => renderHook(() => useAppShellScroll())).toThrow(
      'useAppShellScroll must be used inside AppShell'
    )
  })
})
