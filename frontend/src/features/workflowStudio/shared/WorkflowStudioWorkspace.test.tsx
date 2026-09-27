import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowStudioWorkspace } from './WorkflowStudioWorkspace'
import { makeStudioView, withStudioProviders } from './testStudioProviders'
import { api } from '../../../api'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkspaceSettings } from '../../../types'

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: vi.fn(() => Promise.resolve({ runtimes: {} })),
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

// #416：StudioChatAside 轮询 agent 发布请求（react-query）。
vi.mock('../../../api/studioPublishRequestApi', () => ({
  fetchPendingPublishRequest: vi.fn().mockResolvedValue(null),
  confirmPublishRequest: vi.fn(),
  cancelPublishRequest: vi.fn(),
}))

const mockApi = vi.mocked(api)

// 该 jsdom 环境不提供 localStorage：用内存 stub（Dock 容器按 surface key
// 记忆位置/折叠态；同 agentPanelDock 测试的模式）。
function installLocalStorageStub() {
  const store = new Map<string, string>()
  const stub: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  }
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: stub,
  })
  return stub
}

installLocalStorageStub()

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

function renderWorkspace(
  overrides?: Record<string, unknown>,
  viewOverrides?: Record<string, unknown>
) {
  const props = {
    workflow,
    executorCatalog,
    agentCatalog: [],
    // #426 codex 终轮 P2：节点详情门控消费的 settle 信号（两份查询均
    // settle 的基线；getAgentCatalog/agent-definitions 的 mock 均已返回）。
    agentCatalogSettle: {
      catalogSettled: true,
      catalogFailed: false,
      definitionsSettled: true,
      definitionsFailed: false,
    },
    selectedNodeKey: null,
    setSelectedNodeKey: vi.fn(),
    readOnly: false,
    definitionYaml: 'key: demo_video_workflow\n',
    setDefinitionYaml: vi.fn(),
    backToDraft: vi.fn(),
    setDagFullscreenOpen: vi.fn(),
    ...overrides,
  } as unknown as Record<string, unknown>
  const view = makeStudioView(viewOverrides)
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

  it('shows the DAG full-width and the agent dock floating above it by default', () => {
    renderWorkspace()

    expect(screen.getByText('DAG 画布 stub')).toBeInTheDocument()
    // #795 PR②：chat 迁入 AgentPanelDock 浮层（role=dialog，非模态），
    // 不再是右侧栏 aside（complementary）。
    const dock = screen.getByRole('dialog', { name: 'Agent 助手' })
    expect(dock).toHaveAttribute('aria-modal', 'false')
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(screen.getByText('chat panel stub')).toBeInTheDocument()
    // DAG 区全屏：无分栏（withInspector 只在选中节点详情时加）。
    expect(document.querySelector('[class*="withInspector"]')).toBeNull()
    // #668：面板开关收敛到 appbar（CommandBar），画布工具条不再有开关。
    expect(
      screen.queryByRole('button', { name: 'toggle agent panel' })
    ).not.toBeInTheDocument()
  })

  // #668：agentOpen 提升到 StudioViewContext（appbar 开关写、布局读）；
  // #795 PR②：关闭 = Dock 卸载（折叠保状态走 Dock 的右下角小条）。
  it('closes the agent dock so the DAG takes the full width', () => {
    renderWorkspace({}, { agentOpen: false })

    expect(screen.queryByRole('dialog', { name: 'Agent 助手' })).toBeNull()
    expect(screen.getByText('DAG 画布 stub')).toBeInTheDocument()
  })

  it('puts node detail on the right half next to the full DAG（Dock 浮层不占轨道）', async () => {
    // chat 在 Dock 后不再有「详情替换画布」模式：详情固定右栏，画布保留。
    renderWorkspace({ selectedNodeKey: 'fetch_items' })

    const detail = screen.getByRole('region', { name: '节点详情' })
    expect(detail).toHaveAttribute('data-placement', 'right')
    expect(detail).toHaveTextContent('知识视频 DAG / 获取题目')
    expect(screen.getByText('基本设置')).toBeInTheDocument()
    // 画布不被替换（canvasReplaced 退役），Dock 照常浮在上方。
    expect(screen.getByText('DAG 画布 stub')).toBeInTheDocument()
    expect(
      screen.getByRole('dialog', { name: 'Agent 助手' })
    ).toBeInTheDocument()
    // 等节点代码异步加载落地，避免 act 警告。
    await screen.findByText(/出厂版本/)
  })

  it('puts node detail on the right half when the agent dock is closed', async () => {
    renderWorkspace({ selectedNodeKey: 'fetch_items' }, { agentOpen: false })

    const detail = screen.getByRole('region', { name: '节点详情' })
    expect(detail).toHaveAttribute('data-placement', 'right')
    expect(screen.queryByRole('dialog', { name: 'Agent 助手' })).toBeNull()
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

  // #426 独立复审 P3-2：目录加载失败横幅统一为 Agent 措辞（executor
  // 术语已随 P-0.5 退役；本 PR 起 definitions 失败也并入该横幅）。
  it('shows the catalog load-error banner with agent wording', () => {
    renderWorkspace({ agentCatalogError: true })

    expect(
      screen.getByText('Agent 目录加载失败，绑定信息不可用。')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })
})
