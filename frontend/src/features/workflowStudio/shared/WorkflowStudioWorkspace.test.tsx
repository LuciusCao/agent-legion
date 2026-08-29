import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowStudioWorkspace } from './WorkflowStudioWorkspace'
import { makeStudioView, withStudioProviders } from './testStudioProviders'
import { api } from '../../../api'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkspaceSettings } from '../../../types'

vi.mock('../../../api', () => ({
  api: vi.fn(),
}))

vi.mock('../../../api/agentCatalogApi', () => ({
  getAgentCatalog: vi.fn().mockResolvedValue({ agents: [] }),
}))

vi.mock('../../../components/dag/DagGraph', () => ({
  DagGraph: () => <div>DAG 画布 stub</div>,
}))

vi.mock('../chat/StudioChatPanel', () => ({
  StudioChatPanel: () => <div>chat panel stub</div>,
}))

const mockApi = vi.mocked(api)

const workflow = {
  key: 'demo_video_workflow',
  label: '知识视频 DAG',
  intake: { modes: [] },
  nodes: [
    {
      key: 'fetch_items',
      label: '获取题目',
      capability: 'fetch_items',
      after: [],
      inputs: [],
      outputs: ['questions.json'],
    },
  ],
  edges: [],
}

const executorCatalog = [
  {
    id: 'code-default',
    kind: 'code' as const,
    global_capacity: 16,
    capabilities: ['fetch_items'],
    capability_details: [
      { name: 'fetch_items', path: 'workflow_nodes/fetch_items.py' },
    ],
  },
]

const baseSettings: WorkspaceSettings = {
  entityType: 'question',
  workflowKey: '',
}

function renderWorkspace(overrides?: Record<string, unknown>) {
  const props = {
    workflow,
    executorCatalog,
    agentCatalog: [],
    selectedNodeKey: null,
    setSelectedNodeKey: vi.fn(),
    readOnly: false,
    definitionYaml: 'key: demo_video_workflow\n',
    setDefinitionYaml: vi.fn(),
    backToDraft: vi.fn(),
    setDagFullscreenOpen: vi.fn(),
    ...overrides,
  } as unknown as Record<string, unknown>
  const view = makeStudioView()
  return {
    setSelectedNodeKey: props.setSelectedNodeKey,
    ...render(
      <TestQueryProvider>
        {withStudioProviders(props, view, <WorkflowStudioWorkspace />)}
      </TestQueryProvider>
    ),
  }
}

describe('WorkflowStudioWorkspace', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useSettingStore.setState({ workspaceId: 'ws1', settings: baseSettings })
    mockApi.mockResolvedValue({
      origin: 'builtin',
      code: 'def run(inputs):\n    return {}\n',
      path: 'workflow_nodes/fetch_items.py',
      version: null,
      has_draft: false,
    })
  })

  it('shows DAG and the agent panel side by side by default', () => {
    renderWorkspace()

    expect(screen.getByText('DAG 画布 stub')).toBeInTheDocument()
    const agentPanel = screen.getByRole('complementary', {
      name: 'Agent 对话面板',
    })
    expect(agentPanel).not.toHaveAttribute('data-collapsed')
    expect(screen.getByText('chat panel stub')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '节点详情' })).toBeNull()
  })

  it('collapses the agent panel so the DAG takes the full width', () => {
    renderWorkspace()

    fireEvent.click(
      screen.getAllByRole('button', { name: 'toggle agent panel' })[0]
    )
    expect(
      screen.getByRole('complementary', { name: 'Agent 对话面板' })
    ).toHaveAttribute('data-collapsed', 'true')
    expect(screen.getByText('DAG 画布 stub')).toBeInTheDocument()
  })

  it('replaces the DAG with node detail (left half) when the agent panel is open', async () => {
    renderWorkspace({ selectedNodeKey: 'fetch_items' })

    const detail = screen.getByRole('region', { name: '节点详情' })
    expect(detail).toHaveAttribute('data-placement', 'left')
    expect(detail).toHaveTextContent('知识视频 DAG / 获取题目')
    expect(screen.getByText('基本设置')).toBeInTheDocument()
    // 等节点代码异步加载落地，避免 act 警告。
    await screen.findByText(/出厂版本/)
  })

  it('puts node detail on the right half next to the DAG when the agent panel is collapsed', async () => {
    renderWorkspace({ selectedNodeKey: 'fetch_items' })

    fireEvent.click(
      screen.getAllByRole('button', { name: 'toggle agent panel' })[0]
    )
    const detail = screen.getByRole('region', { name: '节点详情' })
    expect(detail).toHaveAttribute('data-placement', 'right')
    expect(
      screen.getByRole('complementary', { name: 'Agent 对话面板' })
    ).toHaveAttribute('data-collapsed', 'true')
    expect(screen.getByText('DAG 画布 stub')).toBeInTheDocument()
    await screen.findByText(/出厂版本/)
  })

  it('returns to the DAG via the breadcrumb back button', async () => {
    const { setSelectedNodeKey } = renderWorkspace({
      selectedNodeKey: 'fetch_items',
    })

    fireEvent.click(screen.getByRole('button', { name: '返回 DAG' }))
    expect(setSelectedNodeKey).toHaveBeenCalledWith(null)
    await screen.findByText(/出厂版本/)
  })
})
