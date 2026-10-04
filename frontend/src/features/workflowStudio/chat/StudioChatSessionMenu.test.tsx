/**
 * #872 会话菜单：改名就地编辑（Enter 保存 / Esc 只退出编辑）、删除行内
 * 二次确认、失败行内展示；#825 会话管理条在 Dock 内 portal 进标题行。
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
import { StudioChatSessionBar } from './StudioChatSessionBar'
import { AgentPanelDock } from '../../agentPanelDock/AgentPanelDock'
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

function renderMenu(
  overrides: Partial<Parameters<typeof StudioChatSessionMenu>[0]> = {}
) {
  const props = {
    sessions: SESSIONS,
    activeSessionId: 's1',
    onSelectSession: vi.fn(),
    onRename: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
  render(<StudioChatSessionMenu {...props} />)
  fireEvent.click(screen.getByLabelText('选择会话'))
  return props
}

describe('StudioChatSessionMenu', () => {
  it('trigger shows the active session; picking a row selects it', () => {
    const props = renderMenu()
    expect(screen.getByLabelText('选择会话')).toHaveTextContent('梳理审核节点')
    const list = screen.getByRole('list', { name: '会话列表' })
    expect(within(list).getByText('已关闭')).toBeInTheDocument()
    fireEvent.click(within(list).getByText('旧对话'))
    expect(props.onSelectSession).toHaveBeenCalledWith('s2')
  })

  it('renames in place: Enter saves the trimmed title', async () => {
    const props = renderMenu()
    fireEvent.click(screen.getByLabelText('重命名会话 梳理审核节点'))
    const input = screen.getByLabelText('会话名称')
    expect(input).toHaveValue('梳理审核节点')
    fireEvent.change(input, { target: { value: '  拆分审核  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() =>
      expect(props.onRename).toHaveBeenCalledWith('s1', '拆分审核')
    )
    await waitFor(() =>
      expect(screen.queryByLabelText('会话名称')).not.toBeInTheDocument()
    )
  })

  it('Esc leaves edit mode without saving and keeps the menu open', () => {
    const props = renderMenu()
    fireEvent.click(screen.getByLabelText('重命名会话 梳理审核节点'))
    fireEvent.keyDown(screen.getByLabelText('会话名称'), { key: 'Escape' })
    expect(screen.queryByLabelText('会话名称')).not.toBeInTheDocument()
    expect(screen.getByRole('list', { name: '会话列表' })).toBeInTheDocument()
    expect(props.onRename).not.toHaveBeenCalled()
  })

  it('delete needs a second confirmation; cancel keeps the session', async () => {
    const props = renderMenu()
    fireEvent.click(screen.getByLabelText('删除会话 梳理审核节点'))
    expect(props.onDelete).not.toHaveBeenCalled()
    // 运行中的会话：确认文案提示会先关闭。
    expect(screen.getByRole('alert')).toHaveTextContent('会先关闭运行中的会话')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(props.onDelete).not.toHaveBeenCalled()

    fireEvent.click(screen.getByLabelText('删除会话 旧对话'))
    expect(screen.getByRole('alert')).not.toHaveTextContent('会先关闭')
    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(props.onDelete).toHaveBeenCalledWith('s2'))
  })

  it('shows the failure inline and stays in confirm mode', async () => {
    renderMenu({
      onDelete: vi.fn().mockRejectedValue(new Error('删除失败：404')),
    })
    fireEvent.click(screen.getByLabelText('删除会话 梳理审核节点'))
    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    expect(await screen.findByText('删除失败：404')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除' })).toBeInTheDocument()
  })

  it('without manage callbacks the menu is select-only', () => {
    renderMenu({ onRename: undefined, onDelete: undefined })
    expect(screen.queryByLabelText(/^重命名会话/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/^删除会话/)).not.toBeInTheDocument()
  })
})

describe('StudioChatSessionBar title-row placement (#825)', () => {
  const barProps = {
    agents: [{ id: 'kimi', label: 'Kimi' }],
    sessions: SESSIONS,
    selectedAgentId: 'kimi',
    activeSessionId: 's1',
    onSelectAgent: vi.fn(),
    onSelectSession: vi.fn(),
    onNewChat: vi.fn(),
    newChatDisabled: false,
  }

  it('portals into the dock title row next to the title', () => {
    render(
      <AgentPanelDock
        surfaceKey="test-bar"
        title="Agent 助手"
        onClose={vi.fn()}
      >
        <StudioChatSessionBar {...barProps} />
      </AgentPanelDock>
    )
    const handle = screen.getByTestId('dock-test-bar-handle')
    expect(within(handle).getByText('Agent 助手')).toBeInTheDocument()
    expect(within(handle).getByLabelText('选择 Agent')).toBeInTheDocument()
    expect(within(handle).getByLabelText('选择会话')).toBeInTheDocument()
    expect(
      within(handle).getByRole('button', { name: '＋ 新对话' })
    ).toBeInTheDocument()
  })

  it('falls back to an inline row outside a dock', () => {
    const { container } = render(<StudioChatSessionBar {...barProps} />)
    expect(container).toContainElement(screen.getByLabelText('选择会话'))
  })
})
