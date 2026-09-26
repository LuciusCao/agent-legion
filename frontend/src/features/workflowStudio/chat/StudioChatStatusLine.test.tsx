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
      '工具通道已失效，点「继续对话」重建即可恢复'
    )
  })

  it('still renders the generic error event as a warning', () => {
    render(<StatusLine message={statusMessage('error', 'agent 崩溃')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('agent 崩溃')
  })

  it('renders turn_timeout as a warning with the backend detail (#693)', () => {
    render(
      <StatusLine
        message={statusMessage('turn_timeout', '运行超过 1 小时已被终止')}
      />
    )
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('⚠')
    expect(alert).toHaveTextContent('运行超过 1 小时已被终止')
  })

  it('falls back to a built-in text when turn_timeout detail is empty', () => {
    render(<StatusLine message={statusMessage('turn_timeout')} />)
    expect(screen.getByRole('alert')).toHaveTextContent(
      '运行超过 1 小时已被终止'
    )
  })

  it('keeps neutral status events as plain status lines', () => {
    const { container } = render(
      <StatusLine message={statusMessage('session_closed')} />
    )
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container).toHaveTextContent('会话已关闭')
  })

  it('renders empty_turn as a warning with the backend detail (#694)', () => {
    render(
      <StatusLine
        message={statusMessage(
          'empty_turn',
          'agent 未实际处理这条消息（可能在等待后台压缩完成）；请稍后重发，或点「继续对话」重建会话'
        )}
      />
    )
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('⚠')
    expect(alert).toHaveTextContent('未实际处理')
  })

  it('renders compaction lifecycle events as plain status lines (#694)', () => {
    const { container } = render(
      <StatusLine
        message={statusMessage(
          'compact_start',
          '正在压缩上下文，期间发送的消息可能被静默丢弃，请等压缩完成后再发送'
        )}
      />
    )
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container).toHaveTextContent('正在压缩上下文')
  })

  it('frames cancel_requested as awaiting the agent wind-down, not failure', () => {
    // #675：取消是请求（ACP SHOULD 语义），agent 仍在收尾；文案不得暗示
    // 立即失败或工作丢失。尚无终止事件时保持「等待收尾」的当前态。
    const { container } = render(
      <StatusLine message={statusMessage('cancel_requested')} />
    )
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container).toHaveTextContent('已请求取消当前运行，等待 agent 收尾')
  })

  it('downgrades a superseded cancel line to history wording', () => {
    // codex P2：turn_end（被本组件隐藏）或新一轮到达后，「等待收尾」与
    // RunBar 的「已取消」冲突——降级为不再表达当前等待的历史措辞。
    const { container } = render(
      <StatusLine
        message={statusMessage('cancel_requested')}
        cancelSuperseded
      />
    )
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container).toHaveTextContent('已请求取消')
    expect(container).not.toHaveTextContent('等待')
  })
})
