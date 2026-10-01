import { fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api'
import { getSkillDetail } from '../../../api/agentCatalogApi'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { WorkflowNodeDetailDrawer } from './WorkflowNodeDetailDrawer'
import { WorkflowNodeDetailBody } from './WorkflowNodeDetailBody'
import { withStudioProviders } from '../shared/testStudioProviders'

// inspector 各 section（code/config/agent 执行详情）统一走 '../../api' 的 api。
vi.mock('../../../api', () => ({
  api: vi.fn(),
  fetchAgentRuntimes: vi.fn(() => Promise.resolve({ runtimes: {} })),
}))
// 技能预览经 agentCatalogApi wrapper（直连 './core'，不经 '../../api' 聚合层）。
vi.mock('../../../api/agentCatalogApi', () => ({
  getAgentCatalog: vi.fn().mockResolvedValue({ agents: [] }),
  getSkillDetail: vi.fn(),
  getWorkspaceExecutionConfiguration: vi.fn(),
}))

// AgentEditor stub：同一 capability 的面板实例打上挂载印记——
// #426 review P1 的断言核心是「切换节点后面板重挂、草稿状态清零」，只对
// 传入 props（agentId/initialCapability）断言无法覆盖 createdAgentId 这类
// 面板内部状态，这里用挂载序号把它显式暴露出来。序号经 useState 初始化
// 器生成（每个实例只记一次，重渲染不计数）。
let editorMountCount = 0
vi.mock('./AgentEditor', () => ({
  AgentEditor: (props: {
    agentId: string | null
    initialCapability?: string
  }) => {
    const [mountId] = useState(() => ++editorMountCount)
    return (
      <div
        data-testid="agent-editor-stub"
        data-mount={mountId}
        data-agent-id={props.agentId ?? ''}
        data-initial-capability={props.initialCapability ?? ''}
      />
    )
  },
}))

const mockApi = vi.mocked(api)
const mockGetSkillDetail = vi.mocked(getSkillDetail)

const workflow: WorkflowDefinitionRecord = {
  key: 'demo_workflow',
  label: 'Demo DAG',
  intake: { modes: [] },
  nodes: [
    {
      key: 'generate_key_info',
      label: '生成关键信息',
      capability: 'generate_key_info',
      node_type: 'agent',
      after: [],
      inputs: ['questions.json'],
      outputs: ['key_info.json'],
    },
    {
      key: 'review',
      label: '评审',
      capability: 'review',
      node_type: 'agent',
      after: ['generate_key_info'],
      inputs: ['key_info.json'],
      outputs: ['review.json'],
    },
    {
      key: 'generate_key_info_v2',
      label: '生成关键信息（复算）',
      capability: 'generate_key_info',
      node_type: 'agent',
      after: ['review'],
      inputs: ['review.json'],
      outputs: ['key_info_v2.json'],
    },
  ],
  edges: [{ source: 'generate_key_info', target: 'review', condition: null }],
}

const agentCatalog: AgentDefinition[] = [
  {
    id: 'agent-key-info',
    runtime: 'pi',
    capability: 'generate_key_info',
    skill: 'demo/review',
    tools: ['read'],
    requires_labels: {},
    provider: 'deepseek',
    model: 'your-model-b',
    thinking: 'low',
    skill_ref: 'v1.2.0',
    skill_commit: 'abc1234567890',
  },
]

const definitionYaml = [
  'key: demo_workflow',
  'nodes:',
  '  generate_key_info:',
  '    type: agent',
  '    capability: generate_key_info',
  '    label: 生成关键信息',
  '  review:',
  '    type: agent',
  '    capability: review',
  '    label: 评审',
  '  generate_key_info_v2:',
  '    type: agent',
  '    capability: generate_key_info',
  '    label: 生成关键信息（复算）',
  '',
].join('\n')

// #426 codex 终轮 P2：settle 信号基线（两份查询均 settle）。本套件默认
// catalog 命中 generate_key_info 的 published Agent；未命中场景（空 catalog）
// 由节点级门控按 definitions settle 组合，加载/错误占位用例按需覆盖信号。
const settledSettle = {
  catalogSettled: true,
  catalogFailed: false,
  definitionsSettled: true,
  definitionsFailed: false,
}

/** 抽屉消费的 studio 字段（selectedNodeKey 驱动开合）。 */
function studioFor(
  nodeKey: string | null,
  setSelectedNodeKey = vi.fn(),
  draftSave: Record<string, unknown> = { status: 'idle', savedAt: null }
) {
  return {
    selectedNodeKey: nodeKey,
    setSelectedNodeKey,
    workflow,
    agentCatalog,
    agentCatalogSettle: settledSettle,
    definitionYaml,
    setDefinitionYaml: vi.fn(),
    compareSummary: null,
    readOnly: false,
    draftSave,
    resolveConflict: vi.fn(),
    adoptServerDraft: vi.fn(),
  }
}

function renderDrawer(
  nodeKey: string | null = 'generate_key_info',
  draftSave?: Record<string, unknown>
) {
  const setSelectedNodeKey = vi.fn()
  render(
    <TestQueryProvider>
      {withStudioProviders(
        studioFor(
          nodeKey,
          setSelectedNodeKey,
          draftSave ?? { status: 'idle', savedAt: null }
        ),
        {},
        <WorkflowNodeDetailDrawer />
      )}
    </TestQueryProvider>
  )
  return { setSelectedNodeKey }
}

/** inspector 级用例直渲染 Body（抽屉只加壳，不介入 inspector 行为）。 */
function bodyFor(
  nodeKey: string,
  catalog: AgentDefinition[] = agentCatalog,
  settle = settledSettle
) {
  return (
    <TestQueryProvider>
      <WorkflowNodeDetailBody
        workflow={workflow}
        nodeKey={nodeKey}
        agentCatalog={catalog}
        agentCatalogSettle={settle}
        definitionYaml={definitionYaml}
        setDefinitionYaml={() => {}}
        readOnly={false}
        activeKind={null}
        onShowPreview={() => {}}
        onClose={() => {}}
      />
    </TestQueryProvider>
  )
}

describe('WorkflowNodeDetailDrawer（#804 抽屉化）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    editorMountCount = 0
    // #409：内联 Agent 编辑面板的渲染依赖 workspace（无 workspace 时隐藏）。
    useSettingStore.setState({ workspaceId: 'ws1' })
    mockApi.mockResolvedValue({})
    mockGetSkillDetail.mockResolvedValue({
      key: 'demo/review',
      ref: 'v1.2.0',
      commit: 'abc1234567890',
      available: true,
      files: [
        { path: 'SKILL.md', size: 8, content: '# Skill', truncated: false },
      ],
    })
  })

  it('选中节点即开抽屉：头栏 = 节点名 + 类型 + ✕ 关闭（无返回/面包屑）', () => {
    const { setSelectedNodeKey } = renderDrawer()

    // 抽屉头栏（inspector 头栏承接）：节点名 + 类型选择器 + ✕ 关闭。
    expect(screen.getByText('生成关键信息')).toBeInTheDocument()
    // #804 定案：无「← 返回」与面包屑（返回语义 = 关抽屉）。
    expect(
      screen.queryByRole('button', { name: '返回 DAG' })
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Demo DAG / 生成关键信息')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '关闭节点配置' }))
    expect(setSelectedNodeKey).toHaveBeenCalledWith(null)
  })

  it('轮 4 P2-F：冲突时抽屉内嵌警示横幅（岛被 Modal 盖住期间的可见出口）', () => {
    renderDrawer('generate_key_info', {
      status: 'error',
      savedAt: null,
      conflict: true,
      conflictDraftYaml: 'key: demo\n',
    })
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(/自动保存已暂停/)
    // 冲突操作出口在抽屉里同样可用。
    expect(
      within(alert).getByRole('button', { name: '保留本页编辑' })
    ).toBeInTheDocument()
  })

  it('轮 4 P2-F：无警示时不渲染横幅（不占头部空间）', () => {
    renderDrawer('generate_key_info')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('hotfix：docked 根节点退出布局流（display:contents）——不参与 SplitLayout 的 grid 行分配', () => {
    // 根因：persistent 的 docked 根常驻 DOM 且其 Slide 内容在流内有高度，
    // grid 行被均分（画布只剩半屏）。paper 是 position:fixed 自定位，根
    // 零价值。revert：摘掉 sx display:contents 即红。
    renderDrawer()
    const docked = document.querySelector('.MuiDrawer-docked')
    expect(docked).not.toBeNull()
    expect(getComputedStyle(docked as Element).display).toBe('contents')
  })

  it('hotfix：Esc 关闭（persistent 不走 Modal，Esc 语义自行承接；Dock 的 Esc 处理器见 defaultPrevented 跳过）', () => {
    const { setSelectedNodeKey } = renderDrawer()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(setSelectedNodeKey).toHaveBeenCalledWith(null)
  })

  it('轮 8 P2：抽屉非模态——无遮罩、不 aria-hidden 画布（Agent Dock 可并行交互）', () => {
    // MUI temporary Drawer 默认是 Modal（遮罩 + 焦点圈禁 + 兄弟
    // aria-hidden + 滚动锁）——把 z900 的 Agent Dock 盖住，破坏「边改节点
    // 边对话」。非模态化后这些都不发生（revert：回 Modal 默认即红）。
    render(
      <TestQueryProvider>
        {withStudioProviders(
          { ...studioFor('generate_key_info') },
          {},
          <>
            <button data-testid="canvas-sibling">画布侧按钮</button>
            <WorkflowNodeDetailDrawer />
          </>
        )}
      </TestQueryProvider>
    )
    expect(document.querySelector('.MuiBackdrop-root')).toBeNull()
    expect(
      screen.getByTestId('canvas-sibling').closest('[aria-hidden="true"]')
    ).toBeNull()
    // 抽屉内容正常渲染。
    expect(screen.getByText('生成关键信息')).toBeInTheDocument()
  })

  it('无选中节点时不渲染内容（Drawer 关闭）', () => {
    renderDrawer(null)
    expect(screen.queryByText('生成关键信息')).toBeNull()
  })

  it('预览子态：精简返回条回节点详情，✕ 仍关抽屉', () => {
    const { setSelectedNodeKey } = renderDrawer()

    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))
    expect(screen.getByLabelText('Prompt 预览')).toBeInTheDocument()
    // 预览条：「← 节点详情」+ 标题后缀，不再用面包屑。
    expect(screen.getByText('生成关键信息 / Prompt')).toBeInTheDocument()

    // 返回条回 inspector，不关抽屉。
    fireEvent.click(screen.getByRole('button', { name: '返回节点详情' }))
    expect(setSelectedNodeKey).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Prompt 预览')).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()

    // 预览态的 ✕ 直接关抽屉。
    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(setSelectedNodeKey).toHaveBeenCalledWith(null)
  })

  it('技能文件预览同样走返回条', async () => {
    renderDrawer()

    fireEvent.click(screen.getByRole('button', { name: '浏览技能文件' }))
    expect(screen.getByText('生成关键信息 / 技能文件')).toBeInTheDocument()
    expect(await screen.findByText('# Skill')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回节点详情' }))
    expect(screen.queryByLabelText('技能文件预览')).not.toBeInTheDocument()
  })
})

describe('WorkflowNodeDetailBody（inspector 级，抽屉壳不介入）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    editorMountCount = 0
    useSettingStore.setState({ workspaceId: 'ws1' })
    mockApi.mockResolvedValue({})
  })

  // #409：Agent 区块结构简化——无「编辑 Agent」开合按钮，编辑面板默认
  // 内联展开；可编辑态不再渲染重复的只读汇总卡片（agent id 汇总行）。
  it('renders the agent editor inline without a toggle button or summary card', () => {
    render(bodyFor('generate_key_info'))

    expect(
      screen.queryByRole('button', { name: '编辑 Agent' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '为此 capability 新建 Agent' })
    ).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-editor-stub')).toBeInTheDocument()
    expect(screen.queryByText('agent-key-info')).not.toBeInTheDocument()
  })

  // #426 review P1：未绑定 Agent 的节点上创建草稿后切到另一个未绑定节点
  // （agentId 仍为 null，React 复用同一面板实例）——面板必须按新节点的
  // capability 全新挂载，不能带着前一个节点面板里的 createdAgentId 继续编辑
  // 前一个 Agent（#409 去掉开合按钮后已无「收起重置」的兜底入口）。
  it('remounts the inline agent editor fresh when switching to a node with a different capability', () => {
    const { rerender } = render(bodyFor('generate_key_info', []))

    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-mount',
      '1'
    )
    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-initial-capability',
      'generate_key_info'
    )

    rerender(bodyFor('review', []))

    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-mount',
      '2'
    )
    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-initial-capability',
      'review'
    )
    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-agent-id',
      ''
    )
  })

  // #426 review P1 补充：Agent 是 workspace 级共享实体（一 capability 一
  // published），同 capability 的节点间切换编辑目标不变——面板不重挂，
  // 在途表单状态（含创建后的草稿模式）不丢。
  it('keeps the panel mounted across nodes sharing the same capability', () => {
    const { rerender } = render(bodyFor('generate_key_info'))

    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-mount',
      '1'
    )

    // 同 capability 的另一节点（不同 nodeKey）：若 key 误用 node.key，
    // 这里会重挂（挂载序号 +1）——用例即红。
    rerender(bodyFor('generate_key_info_v2'))

    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-mount',
      '1'
    )
  })

  // #426 review P2：agent 目录/定义查询未 settle 时 agentId=null 只是「未知」，
  // 不是「未绑定」——不渲染可操作的新建表单（否则 settle 后表单被 key 替换
  // 丢输入，甚至先提交重复草稿），只给加载占位。
  it('renders a loading placeholder instead of the create form while the binding query is pending', () => {
    render(
      bodyFor('generate_key_info', [], {
        ...settledSettle,
        catalogSettled: false,
      })
    )

    expect(screen.getByText('Agent 绑定解析中...')).toBeInTheDocument()
    expect(screen.queryByTestId('agent-editor-stub')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Agent ID')).not.toBeInTheDocument()
  })

  // #426 review P2：查询失败与「确认未绑定」必须区分——失败时显示错误提示
  // （顶部有全局重试横幅），不退回可操作表单（否则失败场景回到 P2）。
  it('renders an error placeholder instead of the create form when the binding query failed', () => {
    render(
      bodyFor('generate_key_info', [], {
        ...settledSettle,
        catalogFailed: true,
      })
    )

    expect(screen.getByText('Agent 目录加载失败')).toBeInTheDocument()
    expect(screen.queryByTestId('agent-editor-stub')).not.toBeInTheDocument()
  })
})
