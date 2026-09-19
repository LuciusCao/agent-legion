import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { WorkerConsoleLink, isLoopbackUrl } from './WorkerConsoleLink'

describe('WorkerConsoleLink', () => {
  it('renders nothing when no console url is configured', () => {
    const { container } = render(<WorkerConsoleLink url="" />)
    expect(container.innerHTML).toBe('')
  })

  it('opens the console in a new tab and flags loopback addresses', () => {
    render(<WorkerConsoleLink url="http://127.0.0.1:8789" />)

    const link = screen.getByTestId('worker-console-link')
    expect(link.getAttribute('href')).toBe('http://127.0.0.1:8789')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(link.textContent).toContain('打开 Worker 控制台')
    // 回环地址只能在 Worker 所在机器的浏览器打开，悬停提示要说清。
    expect(link.getAttribute('title')).toContain('Worker 所在机器')
  })

  it('keeps a plain title for reachable (non-loopback) addresses', () => {
    render(<WorkerConsoleLink url="http://10.0.0.8:8787" label="控制台" />)

    const link = screen.getByTestId('worker-console-link')
    expect(link.getAttribute('title')).toBe('http://10.0.0.8:8787')
    expect(link.textContent).toContain('控制台')
  })

  it('detects loopback hosts only', () => {
    expect(isLoopbackUrl('http://127.0.0.1:8789')).toBe(true)
    expect(isLoopbackUrl('http://localhost:8789/')).toBe(true)
    expect(isLoopbackUrl('http://10.0.0.8:8787')).toBe(false)
    expect(isLoopbackUrl('not a url')).toBe(false)
  })
})
