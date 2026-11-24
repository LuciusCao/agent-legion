import { describe, expect, it } from 'vitest'
import type { ChatMessage, PermissionView } from './studioChatMessages'
import {
  isTerminalPermission,
  permissionPresentation,
  permissionResolutionText,
  permissionSummaryText,
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

describe('permissionPresentation', () => {
  it('presents an exact command as the command being approved', () => {
    const view = permissionPresentation(
      permission({ command: 'git diff --check' })
    )
    expect(view.state).toBe('命令已精确绑定')
    expect(view.unbound).toBe(false)
    expect(view.summary).toBe(false)
    expect(view.details).toContain('完整命令 · 此次授权将精确绑定')
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
      '批准后取得 · 现已绑定的完整命令\ngit diff --check'
    )
    expect(view.lateDetails).toContain('完整命令在批准后才出现')
    expect(input.command).toBeNull()
  })

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
