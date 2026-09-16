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
  })
})
