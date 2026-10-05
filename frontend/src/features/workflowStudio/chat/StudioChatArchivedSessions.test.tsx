/**
 * #924 会话归档：归档是主整理操作（已关闭的直接归档；运行中的先行内确认
 * 「会先关闭」）；底部「已归档（N）」折叠区可恢复、可永久删除（danger +
 * 二次确认「不可恢复」）；归档会话不可直接选中。
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

function session(
  overrides: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's1',
    workspace_id: 'ws1',
    user_id: 'u1',
    agent_id: 'kimi',
    title: '',
    status: 'idle',
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

const SESSIONS = [
  session({ id: 's1', title: '梳理审核节点' }),
  session({ id: 's2', title: '旧对话', status: 'closed' }),
]
const ARCHIVED = [
  session({
    id: 's9',
    title: '上周的草稿',
    status: 'closed',
    archived_at: '2026-01-02T00:00:00Z',
  }),
]

function renderMenu(
  overrides: Partial<Parameters<typeof StudioChatSessionMenu>[0]> = {}
) {
  const props = {
    sessions: SESSIONS,
    activeSessionId: 's1',
    onSelectSession: vi.fn(),
    onRename: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    archivedSessions: ARCHIVED,
    onArchive: vi.fn().mockResolvedValue(undefined),
    onUnarchive: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
  render(<StudioChatSessionMenu {...props} />)
  fireEvent.click(screen.getByLabelText('选择会话'))
  return props
}

describe('StudioChatSessionMenu archive (#924)', () => {
  it('archives a closed session in one click (recoverable, no confirm)', async () => {
    const props = renderMenu()
    fireEvent.click(screen.getByLabelText('归档会话 旧对话'))
    await waitFor(() => expect(props.onArchive).toHaveBeenCalledWith('s2'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('archiving a live session confirms that it will be closed first', async () => {
    const props = renderMenu()
    fireEvent.click(screen.getByLabelText('归档会话 梳理审核节点'))
    expect(props.onArchive).not.toHaveBeenCalled()
    const alert = screen.getByRole('alert')
    expect(alert).toHaveAttribute('data-tone', 'primary')
    expect(alert).toHaveTextContent('会先关闭运行中的会话')
    expect(alert).toHaveTextContent('可在「已归档」中恢复')
    fireEvent.click(within(alert).getByRole('button', { name: '归档' }))
    await waitFor(() => expect(props.onArchive).toHaveBeenCalledWith('s1'))
  })

  it('archive is offered ahead of the secondary danger delete', () => {
    renderMenu()
    const list = screen.getByRole('list', { name: '会话列表' })
    const labels = within(list)
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label'))
      .filter((label): label is string => Boolean(label))
      .filter((label) => label.endsWith('旧对话'))
    expect(labels).toEqual([
      '重命名会话 旧对话',
      '归档会话 旧对话',
      '删除会话 旧对话',
    ])
  })

  it('the archived section is collapsed by default and restores on demand', async () => {
    const props = renderMenu()
    const toggle = screen.getByRole('button', { name: /已归档（1）/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('上周的草稿')).not.toBeInTheDocument()
    // 默认列表里没有归档会话。
    expect(
      within(screen.getByRole('list', { name: '会话列表' })).queryByText(
        '上周的草稿'
      )
    ).not.toBeInTheDocument()

    fireEvent.click(toggle)
    const archived = screen.getByRole('list', { name: '已归档会话' })
    // 归档会话不可直接选中：点名字不触发选择。
    fireEvent.click(within(archived).getByText('上周的草稿'))
    expect(props.onSelectSession).not.toHaveBeenCalled()
    fireEvent.click(within(archived).getByLabelText('恢复会话 上周的草稿'))
    await waitFor(() => expect(props.onUnarchive).toHaveBeenCalledWith('s9'))
  })

  it('deleting from the archive is a danger action with a second confirm', async () => {
    const props = renderMenu()
    fireEvent.click(screen.getByRole('button', { name: /已归档（1）/ }))
    fireEvent.click(screen.getByLabelText('删除会话 上周的草稿'))
    expect(props.onDelete).not.toHaveBeenCalled()
    const alert = screen.getByRole('alert')
    expect(alert).toHaveAttribute('data-tone', 'danger')
    expect(alert).toHaveTextContent('删除后不可恢复')
    fireEvent.click(within(alert).getByRole('button', { name: '永久删除' }))
    await waitFor(() => expect(props.onDelete).toHaveBeenCalledWith('s9'))
  })

  it('a failed restore is shown inline', async () => {
    renderMenu({
      onUnarchive: vi.fn().mockRejectedValue(new Error('恢复失败：409')),
    })
    fireEvent.click(screen.getByRole('button', { name: /已归档（1）/ }))
    fireEvent.click(screen.getByLabelText('恢复会话 上周的草稿'))
    expect(await screen.findByText('恢复失败：409')).toBeInTheDocument()
  })

  it('no archived sessions or no archive callbacks: no archive UI', () => {
    renderMenu({ archivedSessions: [] })
    expect(screen.queryByText(/已归档（/)).not.toBeInTheDocument()
  })

  it('without archive callbacks the archive actions are hidden', () => {
    renderMenu({ onArchive: undefined, onUnarchive: undefined })
    expect(screen.queryByLabelText(/^归档会话/)).not.toBeInTheDocument()
    expect(screen.queryByText(/已归档（/)).not.toBeInTheDocument()
  })
})
