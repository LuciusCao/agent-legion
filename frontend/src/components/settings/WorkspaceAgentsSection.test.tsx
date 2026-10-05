import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { archiveAgent, fetchAgentDefinitions } from '../../api'
import type { AgentListItem, WorkflowDefinitionRecord } from '../../types'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { WorkspaceAgentsSection } from './WorkspaceAgentsSection'

const workflowState = vi.hoisted(() => ({
  current: {
    data: undefined as WorkflowDefinitionRecord | null | undefined,
    isError: false,
  },
}))

vi.mock('../../api', () => ({
  fetchAgentDefinitions: vi.fn(),
  archiveAgent: vi.fn(),
}))

vi.mock('../../hooks/useWorkflowDefinitionQuery', () => ({
  useWorkflowDefinitionQuery: vi.fn(() => workflowState.current),
}))

const mockFetch = vi.mocked(fetchAgentDefinitions)
const mockArchive = vi.mocked(archiveAgent)

const WORKSPACE_ID = 'polaris'

function agent(
  agentId: string,
  capability: string,
  status: AgentListItem['status'] = 'published',
  published: { capability: string; version: number } | null = status ===
  'published'
    ? { capability, version: 2 }
    : null
): AgentListItem {
  return {
    agent_id: agentId,
    capability,
    runtime: 'pi',
    skill: '',
    version: 2,
    status,
    has_draft: status === 'draft',
    published_at: null,
    published_capability: published?.capability ?? null,
    published_version: published?.version ?? null,
  }
}

function node(
  key: string,
  capability: string,
  nodeType = 'agent'
): WorkflowDefinitionRecord['nodes'][number] {
  return {
    key,
    label: key,
    capability,
    node_type: nodeType,
    inputs: [],
    outputs: [],
    after: [],
  }
}

function workflow(
  nodes: WorkflowDefinitionRecord['nodes']
): WorkflowDefinitionRecord {
  return {
    key: WORKSPACE_ID,
    label: 'polaris',
    nodes,
    edges: [],
    intake: {} as WorkflowDefinitionRecord['intake'],
  }
}

function renderSection() {
  return render(
    <TestQueryProvider>
      <WorkspaceAgentsSection workspaceId={WORKSPACE_ID} />
    </TestQueryProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockFetch.mockResolvedValue({
    agents: [
      agent('analyze_bazi', 'analyze_bazi'),
      agent('write_opening', 'write_opening'),
      agent('write_synthesis', 'write_synthesis', 'draft'),
      agent('old_retired', 'old_retired', 'archived'),
    ],
  })
  mockArchive.mockResolvedValue({ archived: 2 })
  workflowState.current = {
    data: workflow([
      node('opening', 'write_opening'),
      // code 节点同名 capability 不构成 Agent 引用。
      node('assemble', 'write_synthesis', 'code'),
    ]),
    isError: false,
  }
})

describe('WorkspaceAgentsSection', () => {
  it('shows the retirement notice while keeping the catalog usable', async () => {
    renderSection()
    const notice = screen.getByRole('note')
    expect(notice).toHaveTextContent('Agent 定义即将退役')
    expect(
      within(notice).getByRole('link', { name: '查看退役计划' })
    ).toHaveAttribute(
      'href',
      'https://github.com/LuciusCao/agent-legion/issues/440'
    )
    // D1：双读阶段目录与归档入口原样保留。
    const list = await screen.findByRole('list', { name: 'Agent 定义列表' })
    expect(within(list).getAllByRole('button', { name: /归档/ })).toHaveLength(
      3
    )
  })

  it('lists all non-archived agents and flags unreferenced ones', async () => {
    renderSection()
    const list = await screen.findByRole('list', { name: 'Agent 定义列表' })
    const items = within(list).getAllByRole('listitem')
    expect(items.map((li) => li.getAttribute('aria-label'))).toEqual([
      'analyze_bazi',
      'write_opening',
      'write_synthesis',
    ])
    expect(mockFetch).toHaveBeenCalledWith(WORKSPACE_ID)
    expect(
      within(screen.getByRole('listitem', { name: 'write_opening' })).getByText(
        '被 1 个节点引用'
      )
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('listitem', { name: 'analyze_bazi' })).getByText(
        '未被引用'
      )
    ).toBeInTheDocument()
    expect(
      within(
        screen.getByRole('listitem', { name: 'write_synthesis' })
      ).getByText('未被引用')
    ).toBeInTheDocument()
  })

  it('filters down to unreferenced agents', async () => {
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    fireEvent.click(screen.getByRole('button', { name: '未被引用（2）' }))
    const items = within(
      screen.getByRole('list', { name: 'Agent 定义列表' })
    ).getAllByRole('listitem')
    expect(items.map((li) => li.getAttribute('aria-label'))).toEqual([
      'analyze_bazi',
      'write_synthesis',
    ])
    expect(
      screen.getByRole('button', { name: '未被引用（2）' })
    ).toHaveAttribute('aria-pressed', 'true')
  })

  it('archives an orphan agent after confirmation and refetches', async () => {
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    fireEvent.click(screen.getByRole('button', { name: '归档 analyze_bazi' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/analyze_bazi/)).toBeInTheDocument()
    // 未被引用的 Agent 不出引用警告。
    expect(within(dialog).queryByRole('alert')).toBeNull()
    expect(mockArchive).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: '归档' }))
    await waitFor(() =>
      expect(mockArchive).toHaveBeenCalledWith(WORKSPACE_ID, 'analyze_bazi')
    )
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2))
  })

  it('cancelling the confirmation does not archive', async () => {
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    fireEvent.click(screen.getByRole('button', { name: '归档 analyze_bazi' }))
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: '取消' })
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(mockArchive).not.toHaveBeenCalled()
  })

  it('warns about referencing nodes before archiving a referenced agent', async () => {
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    fireEvent.click(screen.getByRole('button', { name: '归档 write_opening' }))
    const alert = within(screen.getByRole('dialog')).getByRole('alert')
    expect(alert).toHaveTextContent(
      '已发布版本仍被当前 workflow 的 1 个节点引用'
    )
    expect(alert).toHaveTextContent('opening')
  })

  it('surfaces archive errors', async () => {
    mockArchive.mockRejectedValueOnce(new Error('Admin role required'))
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    fireEvent.click(screen.getByRole('button', { name: '归档 analyze_bazi' }))
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: '归档' })
    )
    expect(await screen.findByText('Admin role required')).toBeInTheDocument()
  })

  it('treats every agent as unreferenced when no revision is published', async () => {
    workflowState.current = { data: null, isError: false }
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    expect(
      screen.getByRole('button', { name: '未被引用（3）' })
    ).toBeInTheDocument()
  })

  it('does not flag orphans while references are unknown', async () => {
    workflowState.current = { data: undefined, isError: true }
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    expect(screen.queryByText('未被引用')).toBeNull()
    expect(screen.getByRole('button', { name: '未被引用（0）' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('暂无法判断引用关系')
    fireEvent.click(screen.getByRole('button', { name: '归档 analyze_bazi' }))
    expect(
      within(screen.getByRole('dialog')).getByRole('alert')
    ).toHaveTextContent('引用关系尚未确认')
  })
  it('judges references by the published capability behind a draft (#906)', async () => {
    // 已发布 capability write_opening、草稿改成 renamed_opening：节点仍按
    // 已发布的 write_opening 路由到它，不能被当成孤儿。
    mockFetch.mockResolvedValue({
      agents: [
        agent('write_opening', 'renamed_opening', 'draft', {
          capability: 'write_opening',
          version: 1,
        }),
        agent('analyze_bazi', 'analyze_bazi'),
      ],
    })
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    const row = screen.getByRole('listitem', { name: 'write_opening' })
    expect(within(row).getByText('被 1 个节点引用')).toBeInTheDocument()
    expect(within(row).getByText('已发布 v1 · 有草稿')).toBeInTheDocument()
    expect(
      within(row).getByText(
        /capability：write_opening（草稿改为 renamed_opening/
      )
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '未被引用（1）' }))
    const orphans = within(
      screen.getByRole('list', { name: 'Agent 定义列表' })
    ).getAllByRole('listitem')
    expect(orphans.map((li) => li.getAttribute('aria-label'))).toEqual([
      'analyze_bazi',
    ])

    fireEvent.click(screen.getByRole('button', { name: '全部（2）' }))
    fireEvent.click(screen.getByRole('button', { name: '归档 write_opening' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('capability：write_opening')
    expect(dialog).toHaveTextContent('含已发布版本')
    expect(dialog).toHaveTextContent('草稿已把 capability 改为 renamed_opening')
    const alert = within(dialog).getByRole('alert')
    expect(alert).toHaveTextContent(
      '已发布版本仍被当前 workflow 的 1 个节点引用'
    )
    expect(alert).toHaveTextContent('opening')
  })

  it('labels draft-only agents and archives them normally', async () => {
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    const row = screen.getByRole('listitem', { name: 'write_synthesis' })
    expect(within(row).getByText('仅草稿（从未发布）')).toBeInTheDocument()
    fireEvent.click(
      screen.getByRole('button', { name: '归档 write_synthesis' })
    )
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).queryByRole('alert')).toBeNull()
    expect(dialog).not.toHaveTextContent('含已发布版本')
    fireEvent.click(within(dialog).getByRole('button', { name: '归档' }))
    await waitFor(() =>
      expect(mockArchive).toHaveBeenCalledWith(WORKSPACE_ID, 'write_synthesis')
    )
  })

  it('judges draft-only agents by their draft capability', async () => {
    mockFetch.mockResolvedValue({
      agents: [agent('write_opening', 'write_opening', 'draft')],
    })
    renderSection()
    await screen.findByRole('list', { name: 'Agent 定义列表' })
    fireEvent.click(screen.getByRole('button', { name: '归档 write_opening' }))
    const alert = within(screen.getByRole('dialog')).getByRole('alert')
    expect(alert).toHaveTextContent('从未发布')
    expect(alert).toHaveTextContent('opening')
  })
})
