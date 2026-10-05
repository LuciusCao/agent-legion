/**
 * #1041 对话保留策略的会话菜单可见性：配置了保留天数时归档视图每行显示
 * 「N 天后清理」倒计时（归档时间 + 保留天数现算）、临期会话可一键恢复；
 * 归档 / 删除操作提示「将于 N 天后自动清理」。未配置（0）时不显示任何
 * 倒计时或清理提示。
 */
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StudioChatSessionMenu } from './StudioChatSessionMenu'
import type { StudioChatSessionRecord } from './studioChatApi'

const DAY_MS = 86_400_000

function session(
  overrides: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's1',
    workspace_id: 'ws1',
    user_id: 'u1',
    agent_id: 'kimi',
    title: '',
    status: 'closed',
    acp_session_id: null,
    capability_snapshot: {},
    allow_all_permissions: false,
    compacting: false,
    mcp_status: 'unknown',
    selected_node_key: null,
    error_detail: '',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    closed_at: null,
    ...overrides,
  }
}

function archivedDaysAgo(id: string, title: string, days: number) {
  return session({
    id,
    title,
    archived_at: new Date(Date.now() - days * DAY_MS).toISOString(),
  })
}

const ARCHIVED = [
  archivedDaysAgo('a1', '上周的草稿', 2),
  archivedDaysAgo('a2', '临期对话', 29.5),
]

function renderMenu(
  overrides: Partial<Parameters<typeof StudioChatSessionMenu>[0]> = {}
) {
  const props = {
    sessions: [
      session({ id: 's1', title: '进行中', status: 'idle' }),
      session({ id: 's2', title: '旧对话' }),
    ],
    activeSessionId: 's1',
    onSelectSession: vi.fn(),
    onRename: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    archivedSessions: ARCHIVED,
    onArchive: vi.fn().mockResolvedValue(undefined),
    onUnarchive: vi.fn().mockResolvedValue(undefined),
    retentionDays: 30,
    ...overrides,
  }
  render(<StudioChatSessionMenu {...props} />)
  fireEvent.click(screen.getByLabelText('选择会话'))
  return props
}

function openArchive() {
  fireEvent.click(screen.getByRole('button', { name: /已归档（2）/ }))
  return screen.getByRole('list', { name: '已归档会话' })
}

describe('StudioChatSessionMenu retention (#1041)', () => {
  it('shows a per-row cleanup countdown and restores an imminent session', async () => {
    const props = renderMenu()
    const archived = openArchive()
    expect(within(archived).getByText('28 天后清理')).toBeInTheDocument()
    const imminent = within(archived).getByText('1 天后清理')
    expect(imminent).toHaveAttribute('data-imminent', 'true')

    fireEvent.click(within(archived).getByLabelText('恢复会话 临期对话'))
    await waitFor(() => expect(props.onUnarchive).toHaveBeenCalledWith('a2'))
  })

  it('no retention configured: no countdown and no cleanup notices', async () => {
    const props = renderMenu({ retentionDays: 0 })
    const archived = openArchive()
    expect(within(archived).queryByText(/天后清理/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('归档会话 旧对话'))
    await waitFor(() => expect(props.onArchive).toHaveBeenCalledWith('s2'))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('删除会话 旧对话'))
    expect(screen.getByRole('alert')).not.toHaveTextContent('自动清理')
  })

  it('archiving a closed session announces the cleanup date', async () => {
    const props = renderMenu()
    fireEvent.click(screen.getByLabelText('归档会话 旧对话'))
    await waitFor(() => expect(props.onArchive).toHaveBeenCalledWith('s2'))
    expect(await screen.findByRole('status')).toHaveTextContent(
      '已归档，将于 30 天后自动清理'
    )
  })

  it('the live-archive and delete confirmations carry the cleanup window', () => {
    renderMenu()
    fireEvent.click(screen.getByLabelText('归档会话 进行中'))
    expect(screen.getByRole('alert')).toHaveTextContent('将于 30 天后自动清理')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    fireEvent.click(screen.getByLabelText('删除会话 旧对话'))
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('删除后不可恢复，数据将于 30 天后自动清理')
  })
})
