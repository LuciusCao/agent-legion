import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatusLine } from './StudioChatStatusLine'
import type { ChatMessage } from './studioChatMessages'

function statusMessage(
  event: string,
  detail = '',
  id = 'm1',
  seq = 1
): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind: 'status',
    role: 'system',
    content: { event, detail },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('StatusLine', () => {
  it('renders run_token_invalidated as a warning with the backend detail', () => {
    render(
      <StatusLine
        message={statusMessage(
          'run_token_invalidated',
          '工具通道已失效（运行凭证过期或被吊销），agent 暂时无法调用平台工具；关闭当前会话后点「继续对话」重建即可恢复。'
        )}
      />
    )
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('⚠')
    expect(alert).toHaveTextContent('工具通道已失效')
    expect(alert).toHaveTextContent('继续对话')
  })

  it('falls back to a built-in text when the backend detail is empty', () => {
    render(<StatusLine message={statusMessage('run_token_invalidated')} />)
    expect(screen.getByRole('alert')).toHaveTextContent(
      '工具通道已失效，关闭会话后点「继续对话」恢复'
    )
  })

  it('still renders the generic error event as a warning', () => {
    render(<StatusLine message={statusMessage('error', 'agent 崩溃')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('agent 崩溃')
  })

  it('keeps neutral status events as plain status lines', () => {
    const { container } = render(
      <StatusLine message={statusMessage('session_closed')} />
    )
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container).toHaveTextContent('会话已关闭')
  })
})
