import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StudioChatTextBubble } from './StudioChatTextBubble'
import type { ChatMessage } from './studioChatMessages'

function message(role: 'user' | 'agent', text: string, id = 'm1'): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind: 'text',
    role,
    content: { text },
    seq: 1,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('StudioChatTextBubble', () => {
  it('renders a completed agent message as markdown', () => {
    const { container } = render(
      <StudioChatTextBubble
        message={message(
          'agent',
          '**加粗** `code`\n\n- 第一项\n- 第二项\n\n```js\nconst a = 1\n```'
        )}
        streaming={false}
      />
    )
    expect(container.querySelector('strong')).toHaveTextContent('加粗')
    expect(container.querySelector('code')).toHaveTextContent('code')
    expect(container.querySelectorAll('li')).toHaveLength(2)
    expect(container.querySelector('pre code')).toHaveTextContent('const a = 1')
  })

  it('keeps user messages as plain text', () => {
    const { container } = render(
      <StudioChatTextBubble
        message={message('user', '1.2*3 和 **not bold**')}
        streaming={false}
      />
    )
    expect(container.querySelector('strong')).toBeNull()
    expect(container).toHaveTextContent('1.2*3 和 **not bold**')
  })

  it('strips injected scripts and event handlers', () => {
    const { container } = render(
      <StudioChatTextBubble
        message={message(
          'agent',
          '<script>alert(1)</script><img src="https://x.test/a.png" onerror="alert(2)">\n\n[x](javascript:alert(3))'
        )}
        streaming={false}
      />
    )
    expect(container.querySelector('script')).toBeNull()
    const img = container.querySelector('img')
    expect(img).not.toBeNull()
    expect(img!.getAttribute('onerror')).toBeNull()
    const link = container.querySelector('a')
    expect(link).not.toBeNull()
    expect(link!.getAttribute('href')).toBeNull()
  })

  it('keeps a streaming agent message as plain text', () => {
    const { container } = render(
      <StudioChatTextBubble
        message={message('agent', '**半句** 还在输出')}
        streaming={true}
      />
    )
    expect(container.querySelector('strong')).toBeNull()
    expect(container).toHaveTextContent('**半句** 还在输出')
  })

  it('switches to markdown once the message completes', () => {
    const { container, rerender } = render(
      <StudioChatTextBubble
        message={message('agent', '**完成**')}
        streaming={true}
      />
    )
    expect(container.querySelector('strong')).toBeNull()
    rerender(
      <StudioChatTextBubble
        message={message('agent', '**完成**')}
        streaming={false}
      />
    )
    expect(container.querySelector('strong')).toHaveTextContent('完成')
  })
})
