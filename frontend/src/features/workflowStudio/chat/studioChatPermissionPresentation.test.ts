import { describe, expect, it } from 'vitest'
import type { ChatMessage, PermissionView } from './studioChatMessages'
import { buildPermissionViews } from './studioChatMessages'
import {
  isTerminalPermission,
  permissionPresentation,
  permissionResolutionText,
  permissionSummaryText,
  permissionTerminalView,
} from './studioChatPermissionPresentation'

function permission(overrides: Partial<PermissionView>): PermissionView {
  return {
    requestId: 'r1',
    toolTitle: 'Bash',
    command: null,
    terminal: true,
    summary: 'Requesting approval to Running: git diff…',
    lateBoundCommand: null,
    options: [{ optionId: 'allow', name: '允许一次', kind: 'allow_once' }],
    resolved: false,
    decisionText: null,
    ...overrides,
  }
}

function event(
  seq: number,
  kind: ChatMessage['kind'],
  content: Record<string, unknown>,
  role: ChatMessage['role'] = 'agent'
): ChatMessage {
  return {
    id: `event-${seq}`,
    session_id: 's1',
    kind,
    role,
    seq,
    created_at: '2026-01-01T00:00:00Z',
    content,
  }
}

function command(seq: number, toolCallId: string, value: string): ChatMessage {
  return event(seq, 'tool_call', {
    toolCallId,
    title: 'Bash',
    kind: 'execute',
    rawInput: { command: value },
  })
}

function permissionRequest(seq: number, toolCallId: string): ChatMessage {
  return event(seq, 'permission', {
    request_id: `request-${seq}`,
    status: 'pending',
    tool_call: { toolCallId, title: 'Bash' },
    options: [{ optionId: 'allow', name: '允许一次', kind: 'allow_once' }],
  })
}

describe('permissionPresentation', () => {
  it('presents an exact command as the command being approved', () => {
    const view = permissionPresentation(
      permission({ command: 'git diff --check' })
    )
    expect(view.state).toBe('命令已精确绑定')
    expect(view.unbound).toBe(false)
    expect(view.summary).toBe(false)
    expect(view.details).toContain('完整命令 · 仅在授权围栏开启时精确绑定')
    expect(view.details).toContain('git diff --check')
    expect(view.details).toContain('你批准的就是上方完整命令')
    expect(view.details).not.toContain('Running: git diff')
  })

  it('marks an unbound request summary as incomplete without inventing an origin', () => {
    const view = permissionPresentation(permission({}))
    expect(view.state).toBe('命令未绑定 · 仅显示截断摘要')
    expect(view.unbound).toBe(true)
    expect(view.summary).toBe(true)
    expect(view.details).toContain('截断摘要 · 不是完整命令')
    expect(view.details).toContain('主 Agent 批准后晚绑定')
    expect(view.details).toContain('子 Agent 未上报事件')
  })

  it('keeps the approval-time summary beside a post-approval late binding', () => {
    const input = permission({
      resolved: true,
      decisionText: '已允许',
      lateBoundCommand: 'git diff --check',
    })
    const view = permissionPresentation(input)
    expect(view.state).toBe('批准后已晚绑定')
    expect(view.details).toContain('批准时所见 · 截断摘要')
    expect(view.details).toContain('Requesting approval to Running: git diff…')
    expect(view.lateDetails).toContain(
      '批准后取得 · 仅在授权围栏开启时绑定的完整命令\ngit diff --check'
    )
    expect(view.lateDetails).toContain('完整命令在批准后才出现')
    expect(input.command).toBeNull()
  })

  it('keeps late binding visible for the allow-all approval channel', () => {
    const resolved = { decision: { option_id: 'allow', via: 'allow_all' } }
    const terminal = permissionTerminalView(
      null,
      'git status',
      resolved,
      'Bash'
    )
    expect(terminal.lateBoundCommand).toBe('git status')
    const view = permissionPresentation(
      permission({
        resolved: true,
        decisionText: '已自动允许（本次全部允许）',
        lateBoundCommand: terminal.lateBoundCommand,
      })
    )
    expect(view.state).toBe('批准后已晚绑定')
    expect(view.lateDetails).toContain('git status')
  })

  it.each([
    ['pending', undefined],
    ['human approval', { option_id: 'allow' }],
    ['allow-all', { option_id: 'allow', via: 'allow_all' }],
  ])(
    '%s ignores a blank snapshot and keeps the first valid late command',
    (_name, decision) => {
      const terminal = permissionTerminalView(
        { title: 'Bash', rawInput: { command: '   ' } },
        'ls -la',
        decision ? { decision } : undefined,
        'Bash'
      )
      expect(terminal.command).toBeNull()
      expect(terminal.lateBoundCommand).toBe('ls -la')
      const view = permissionPresentation(
        permission({
          resolved: decision !== undefined,
          decisionText: terminal.decisionText,
          command: terminal.command,
          lateBoundCommand: terminal.lateBoundCommand,
        })
      )
      expect(decision ? view.lateDetails : view.details).toContain('ls -la')
      expect(view.details).not.toContain('cat secrets')
      expect(view.lateDetails).not.toContain('cat secrets')
    }
  )

  it('keeps an approved child-agent request honestly unbound', () => {
    const view = permissionPresentation(
      permission({ resolved: true, decisionText: '已允许' })
    )
    expect(view.state).toBe('命令仍未绑定')
    expect(view.details).toContain('没有可展示的完整命令')
    expect(view.details).not.toContain('批准后取得')
  })

  it('does not present a command observed after denial as approved', () => {
    const view = permissionPresentation(
      permission({
        resolved: true,
        decisionText: '已拒绝',
        lateBoundCommand: 'git status',
      })
    )
    expect(view.state).toBe('')
    expect(view.unbound).toBe(false)
    expect(view.details).not.toContain('git status')
    expect(view.details).not.toContain('批准后已晚绑定')
  })

  it('leaves command-less non-terminal cards without terminal warnings', () => {
    const view = permissionPresentation(
      permission({ toolTitle: 'Read', terminal: false, summary: null })
    )
    expect(view.state).toBe('')
    expect(view.details).toBe('')
    expect(view.unbound).toBe(false)
  })

  it.each([
    [
      'exact',
      permission({ command: 'ls -la' }),
      'details',
      '不允许追加、删改或替换',
    ],
    ['unbound', permission({}), 'details', '一次批准仅对应一次终端执行'],
    [
      'late',
      permission({
        resolved: true,
        decisionText: '已允许',
        lateBoundCommand: 'ls -la',
      }),
      'lateDetails',
      '不可替换',
    ],
    [
      'child',
      permission({ resolved: true, decisionText: '已允许' }),
      'details',
      '一次批准仅对应一次终端执行',
    ],
  ] as const)(
    '%s explains explicit-on restrictions and default-off terminal freedom',
    (_name, input, field, restriction) => {
      const text = permissionPresentation(input)[field]
      expect(text).toContain('围栏开启时')
      expect(text).toContain(restriction)
      expect(text).toContain('围栏关闭时')
      expect(text).toContain('额外终端仍可执行')
    }
  )

  it('conditions exact and late binding labels on the terminal fence', () => {
    const exact = permissionPresentation(permission({ command: 'ls -la' }))
    expect(exact.ariaLabel).toContain('围栏开启时')
    const late = permissionPresentation(
      permission({
        resolved: true,
        decisionText: '已允许',
        lateBoundCommand: 'ls -la',
      })
    )
    expect(late.lateDetails.split('\n')[0]).toContain('围栏开启时')
  })
})

describe('permission turn evidence', () => {
  it.each([
    ['pending', undefined],
    ['human approval', { option_id: 'allow' }],
    ['allow-all', { option_id: 'allow', via: 'allow_all' }],
  ])(
    '%s does not borrow an opaque toolCallId command across turns',
    (_name, decision) => {
      const old = command(1, 'tc-1', 'ls -la')
      const turnEnd = event(2, 'status', { event: 'turn_end' }, 'system')
      const nextTurn = event(3, 'text', { text: 'next turn' }, 'user')
      const request = permissionRequest(4, 'tc-1')
      const resolved = decision
        ? event(
            5,
            'permission',
            { request_id: 'request-4', status: 'resolved', decision },
            'user'
          )
        : null
      const history = [
        old,
        turnEnd,
        nextTurn,
        request,
        ...(resolved ? [resolved] : []),
      ]
      const [before] = buildPermissionViews(history)
      expect(before.lateBoundCommand).toBeNull()
      expect(permissionPresentation(before).details).not.toContain('ls -la')

      const first = command(10, 'tc-1', 'printf new')
      const later = command(11, 'tc-1', 'cat secrets')
      const [after] = buildPermissionViews([later, ...history, first])
      expect(after.lateBoundCommand).toBe('printf new')
      const view = permissionPresentation(after)
      expect(decision ? view.lateDetails : view.details).toContain('printf new')
      expect(view.details).not.toContain('cat secrets')
      expect(view.lateDetails).not.toContain('cat secrets')
    }
  )

  it('uses the structured turn id in toolCallId before timeline boundaries', () => {
    const old = command(1, '1:tc', 'ls -la')
    const request = permissionRequest(2, '2:tc')
    expect(buildPermissionViews([old, request])[0].lateBoundCommand).toBeNull()
    const first = command(3, '2:tc', 'printf new')
    const later = command(4, '2:tc', 'cat secrets')
    const [after] = buildPermissionViews([old, request, later, first])
    expect(after.lateBoundCommand).toBe('printf new')
    expect(permissionPresentation(after).details).not.toContain('cat secrets')
  })
})

describe('permission input readers', () => {
  it('uses kind before title and known terminal titles only as fallback', () => {
    expect(isTerminalPermission('Bash', '')).toBe(true)
    expect(isTerminalPermission('Bash', 'read')).toBe(false)
    expect(isTerminalPermission('Custom', 'execute')).toBe(true)
    expect(isTerminalPermission('Read', '')).toBe(false)
    expect(isTerminalPermission('Edit', '')).toBe(false)
  })

  it('reads ACP nested content as a summary, never as rawInput.command', () => {
    const content = [
      {
        type: 'content',
        content: { type: 'text', text: 'Requesting approval to Running: npm…' },
      },
    ]
    expect(permissionSummaryText(content)).toBe(
      'Requesting approval to Running: npm…'
    )
  })

  it('preserves automatic resolution channels', () => {
    const message: ChatMessage = {
      id: 'm1',
      session_id: 's1',
      kind: 'permission',
      role: 'system',
      seq: 1,
      created_at: '2026-01-01T00:00:00Z',
      content: {
        status: 'resolved',
        decision: { option_id: 'allow', via: 'allow_all' },
        tool_call: { title: 'Bash' },
      },
    }
    expect(permissionResolutionText(message)).toBe(
      '已自动允许（本次全部允许）：Bash'
    )
  })
})
