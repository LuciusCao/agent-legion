import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StudioChatRunBar } from './StudioChatRunBar'

function renderBar(props: {
  status?: string | null
  busy?: boolean
  lastRunMs?: number | null
  lastTerminalEvent?: string | null
}) {
  return render(
    <StudioChatRunBar
      status={props.status ?? 'idle'}
      busy={props.busy ?? false}
      lastRunMs={props.lastRunMs ?? null}
      lastTerminalEvent={props.lastTerminalEvent ?? null}
      onCancel={vi.fn()}
    />
  )
}

describe('StudioChatRunBar', () => {
  it('shows 已完成 with the duration after a normal turn end', () => {
    renderBar({ lastRunMs: 65_000, lastTerminalEvent: 'turn_end' })
    expect(screen.getByLabelText('运行状态')).toHaveTextContent(
      '已完成 · 用时 1m5s'
    )
  })

  it('shows 已超时终止 instead of 已完成 after a timeout-terminated turn (#693)', () => {
    renderBar({ lastRunMs: 3_600_000, lastTerminalEvent: 'turn_timeout' })
    const bar = screen.getByLabelText('运行状态')
    expect(bar).toHaveTextContent('已超时终止 · 用时 60m0s')
    expect(bar).not.toHaveTextContent('已完成')
  })

  it('keeps 已完成 for a user-cancelled (non-timeout) turn', () => {
    renderBar({ lastRunMs: 5_000, lastTerminalEvent: 'turn_end' })
    expect(screen.getByLabelText('运行状态')).toHaveTextContent('已完成')
  })

  it('shows the busy state with a cancel button while running', () => {
    renderBar({ status: 'running', busy: true })
    expect(screen.getByLabelText('运行状态')).toHaveTextContent('运行中')
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument()
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StudioChatRunBar } from './StudioChatRunBar'

/** #675：取消轮的收尾展示——stopReason=cancelled 的 turn_end 不再伪装成
 * 「已完成」（已完成的工具成果其实是真实存在的），也不再静默无提示。 */
describe('StudioChatRunBar', () => {
  it('renders busy state with the cancel button', () => {
    const onCancel = vi.fn()
    render(
      <StudioChatRunBar
        status="running"
        busy
        lastRunMs={null}
        lastRunCancelled={false}
        onCancel={onCancel}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('renders the cancelled run distinctly from a completed run', () => {
    render(
      <StudioChatRunBar
        status="idle"
        busy={false}
        lastRunMs={65000}
        lastRunCancelled
        onCancel={() => {}}
      />
    )
    const bar = screen.getByLabelText('运行状态')
    expect(bar).toHaveTextContent('已取消')
    expect(bar).toHaveTextContent('已运行 1m5s')
    expect(bar).toHaveTextContent('可继续追问结果')
    expect(bar).not.toHaveTextContent('已完成')
  })

  it('renders a completed run with its duration when not cancelled', () => {
    render(
      <StudioChatRunBar
        status="idle"
        busy={false}
        lastRunMs={3000}
        lastRunCancelled={false}
        onCancel={() => {}}
      />
    )
    expect(screen.getByLabelText('运行状态')).toHaveTextContent(
      '已完成 · 用时 3s'
    )
  })

  it('keeps the error branch ahead of the cancelled branch', () => {
    render(
      <StudioChatRunBar
        status="error"
        busy={false}
        lastRunMs={null}
        lastRunCancelled
        onCancel={() => {}}
      />
    )
    expect(screen.getByLabelText('运行状态')).toHaveTextContent('会话出错')
  })

  it('renders nothing without a session status', () => {
    const { container } = render(
      <StudioChatRunBar
        status={null}
        busy={false}
        lastRunMs={null}
        lastRunCancelled={false}
        onCancel={() => {}}
      />
    )
    expect(container.querySelector('[aria-label="运行状态"]')).toBeNull()
  })
})
