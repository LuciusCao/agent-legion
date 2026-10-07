import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { fetchAgentDefinitions, fetchAgentProvenance } from '../../api'
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
  fetchAgentProvenance: vi.fn(),
}))

vi.mock('../../hooks/useWorkflowDefinitionQuery', () => ({
  useWorkflowDefinitionQuery: vi.fn(() => workflowState.current),
}))

const mockFetch = vi.mocked(fetchAgentDefinitions)
const mockProvenance = vi.mocked(fetchAgentProvenance)

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

const RUNTIME = {
  runtime: 'pi',
  provider: '',
  model: '',
  thinking: '',
  prompt: '',
  prompt_mode: '',
}

function node(
  key: string,
  capability: string,
  options: { nodeType?: string; inlined?: boolean } = {}
): WorkflowDefinitionRecord['nodes'][number] {
  return {
    key,
    label: `${key} 节点`,
    capability,
    node_type: options.nodeType ?? 'agent',
    inputs: [],
    outputs: [],
    after: [],
    ...(options.inlined ? { execution: RUNTIME } : {}),
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

async function rowOf(agentId: string) {
  const list = await screen.findByRole('list', { name: '历史 Agent 定义列表' })
  return within(list).getByRole('listitem', { name: agentId })
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
  mockProvenance.mockResolvedValue({
    nodes: [
      {
        node_key: 'opening',
        node_label: 'opening 节点',
        agent_id: 'write_opening',
        agent_version: 2,
      },
      {
        node_key: 'opening_v2',
        node_label: 'opening_v2 节点',
        agent_id: 'write_opening',
        agent_version: 2,
      },
    ],
  })
  workflowState.current = {
    data: workflow([
      node('opening', 'write_opening', { inlined: true }),
      node('opening_v2', 'write_opening', { inlined: true }),
      // 未内联的 legacy 节点仍按 capability 回读 Agent 定义。
      node('bazi', 'analyze_bazi'),
      // code 节点同名 capability 不构成 Agent 引用。
      node('assemble', 'write_synthesis', { nodeType: 'code' }),
    ]),
    isError: false,
  }
})

describe('WorkspaceAgentsSection（#1079 / #440 D1 只读历史）', () => {
  it('is a read-only history without archive actions', async () => {
    renderSection()
    expect(
      screen.getByRole('heading', { name: '历史 Agent 定义' })
    ).toBeInTheDocument()
    const notice = screen.getByRole('note')
    expect(notice).toHaveTextContent('Agent 定义已退役为只读历史')
    expect(
      within(notice).getByRole('link', { name: '查看退役计划' })
    ).toHaveAttribute(
      'href',
      'https://github.com/LuciusCao/agent-legion/issues/440'
    )
    const list = await screen.findByRole('list', {
      name: '历史 Agent 定义列表',
    })
    // 归档会让旧快照回读失败（D1）：列表上没有任何按钮。
    expect(within(list).queryAllByRole('button')).toHaveLength(0)
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(within(list).queryByText('old_retired')).not.toBeInTheDocument()
  })

  it('shows how many active-revision nodes inlined each definition', async () => {
    renderSection()
    const opening = await rowOf('write_opening')
    const chip = await within(opening).findByText('已内联到 2 个节点')
    expect(chip).toHaveAttribute(
      'title',
      'opening 节点（opening）、opening_v2 节点（opening_v2）'
    )
    expect(
      within(await rowOf('analyze_bazi')).getByText('未内联到当前 workflow')
    ).toBeInTheDocument()
    expect(mockProvenance).toHaveBeenCalledWith(WORKSPACE_ID)
  })

  it('flags definitions still read by legacy (not inlined) nodes', async () => {
    renderSection()
    expect(
      within(await rowOf('analyze_bazi')).getByText('仍被 1 个未内联节点使用')
    ).toBeInTheDocument()
    // 已内联节点与 code 节点都不算回读引用。
    expect(
      within(await rowOf('write_opening')).queryByText(/未内联节点使用/)
    ).not.toBeInTheDocument()
    expect(
      within(await rowOf('write_synthesis')).queryByText(/未内联节点使用/)
    ).not.toBeInTheDocument()
  })

  it('shows no inlined counts while provenance is unknown', async () => {
    mockProvenance.mockRejectedValue(new Error('boom'))
    renderSection()
    const row = await rowOf('write_opening')
    expect(within(row).queryByText(/已内联到/)).not.toBeInTheDocument()
    expect(
      within(row).queryByText('未内联到当前 workflow')
    ).not.toBeInTheDocument()
    expect(
      await screen.findByText(
        '当前 workflow 加载失败，暂无法判断内联与引用关系。'
      )
    ).toBeInTheDocument()
  })

  it('labels draft-only agents and shows the published version', async () => {
    renderSection()
    expect(
      within(await rowOf('write_synthesis')).getByText('仅草稿（从未发布）')
    ).toBeInTheDocument()
    expect(
      within(await rowOf('analyze_bazi')).getByText(
        'capability：analyze_bazi · v2'
      )
    ).toBeInTheDocument()
  })

  // #906：legacy 节点按已发布 capability 路由，草稿改名不影响引用判定。
  it('judges legacy references by the published capability behind a draft', async () => {
    mockFetch.mockResolvedValue({
      agents: [
        agent('renamed', 'new_cap', 'draft', {
          capability: 'bazi_cap',
          version: 1,
        }),
      ],
    })
    workflowState.current = {
      data: workflow([node('bazi', 'bazi_cap')]),
      isError: false,
    }
    renderSection()
    const row = await rowOf('renamed')
    expect(within(row).getByText('仍被 1 个未内联节点使用')).toBeInTheDocument()
    expect(
      within(row).getByText('capability：bazi_cap · v1')
    ).toBeInTheDocument()
  })
})
