import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api'
import { getSkillDetail } from '../../../api/agentCatalogApi'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { WorkflowNodeDetailDrawer } from './WorkflowNodeDetailDrawer'
import { WorkflowNodeDetailBody } from './WorkflowNodeDetailBody'
import {
  withStudioProviders,
  makeStudioView,
} from '../shared/testStudioProviders'

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

// #1079（#440 P3b）：workflow 顶层 execution.runtime 让三个 agent 节点都
// 自含（执行档案编辑区含「查看 Prompt / 浏览技能文件」）；legacy 用例另配。
const definitionYaml = [
  'key: demo_workflow',
  'execution:',
  '  runtime: pi',
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
function bodyFor(nodeKey: string, yaml: string = definitionYaml) {
  return (
    <TestQueryProvider>
      <WorkflowNodeDetailBody
        workflow={workflow}
        nodeKey={nodeKey}
        agentCatalog={agentCatalog}
        definitionYaml={yaml}
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
    // 节点 skill 编辑行的渲染依赖 workspace（无 workspace 时隐藏）。
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

  it('hotfix 轮 2 codex P2：抽屉内有更上层模态（.MuiModal-root/.MuiPopover）时 Esc 让位——只关最上层不关抽屉', () => {
    const { setSelectedNodeKey } = renderDrawer()
    // 结构桩：抽屉内开了内容 dialog/菜单（jsdom 无真 Modal，插标记元素）。
    const modalStub = document.createElement('div')
    modalStub.className = 'MuiModal-root'
    document.body.appendChild(modalStub)
    try {
      fireEvent.keyDown(document, { key: 'Escape' })
      // 让位：抽屉不关（摘掉让位探测即红——抽屉会被直接关掉）。
      expect(setSelectedNodeKey).not.toHaveBeenCalled()
    } finally {
      modalStub.remove()
    }
    // 模态关掉后 Esc 恢复关抽屉。
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(setSelectedNodeKey).toHaveBeenCalledWith(null)
  })

  it('hotfix：Esc 关闭（persistent 不走 Modal，Esc 语义自行承接；Dock 的 Esc 处理器见 defaultPrevented 跳过）', () => {
    const { setSelectedNodeKey } = renderDrawer()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(setSelectedNodeKey).toHaveBeenCalledWith(null)
  })

  it('P2-2：窄屏 Agent 页签抽屉不可见（display:none）但选中态保留，切回画布页签复现', () => {
    // D1 后抽屉挂在 SplitLayout 层，不随画布列 display:none——hidden 必须
    // 自带：paper display:none 且不卸载（selectedNodeKey 与预览子态保留）。
    // revert 即红：只出 Esc 栈不藏 paper 时 display 为空串。
    const studio = studioFor('generate_key_info')
    const ui = (panel: 'graph' | 'agent') => (
      <TestQueryProvider>
        {withStudioProviders(
          studio,
          makeStudioView({ narrow: true, mobilePanel: panel }),
          <WorkflowNodeDetailDrawer />
        )}
      </TestQueryProvider>
    )
    const { rerender } = render(ui('agent'))
    const paper = () =>
      document.querySelector('.MuiDrawer-paper') as HTMLElement
    expect(paper().style.display).toBe('none')
    // 不卸载：内容仍在 DOM（打开状态保留）。
    expect(screen.getByText('生成关键信息')).toBeInTheDocument()
    rerender(ui('graph'))
    expect(paper().style.display).toBe('')
    expect(screen.getByText('生成关键信息')).toBeInTheDocument()
  })

  it('#817：paper 带页签行实测底边作顶边让位变量（窄屏 CSS 断点消费，Agent 页签不被盖住）', () => {
    // revert（paper 不写 --studio-drawer-top-inset）即红：窄屏抽屉回到
    // top:0，物理盖住「Agent」页签。
    const row = document.createElement('div')
    row.setAttribute('data-testid', 'studio-mobile-nav-row')
    row.getBoundingClientRect = () => ({ bottom: 105 }) as DOMRect
    document.body.appendChild(row)
    try {
      render(
        <TestQueryProvider>
          {withStudioProviders(
            studioFor('generate_key_info'),
            makeStudioView({ narrow: true, mobilePanel: 'graph' }),
            <WorkflowNodeDetailDrawer />
          )}
        </TestQueryProvider>
      )
      const paper = document.querySelector('.MuiDrawer-paper') as HTMLElement
      expect(paper.style.getPropertyValue('--studio-drawer-top-inset')).toBe(
        '105px'
      )
      expect(paper.style.display).toBe('')
    } finally {
      row.remove()
    }
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

    // 预览态的 ✕ 直接关抽屉；#770：与详情态头栏 ✕ 同一文案（单一关闭语义）。
    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))
    fireEvent.click(screen.getByRole('button', { name: '关闭节点配置' }))
    expect(setSelectedNodeKey).toHaveBeenCalledWith(null)
  })

  it('#770 分级 Esc：预览子态 Esc 先回节点详情，详情态再按 Esc 才关抽屉', () => {
    const { setSelectedNodeKey } = renderDrawer()

    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))
    expect(screen.getByLabelText('Prompt 预览')).toBeInTheDocument()

    // 第一下 Esc：回节点详情（同「← 节点详情」），抽屉不关。base 上
    // Esc 直接关抽屉（丢掉预览导航上下文）即红。
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(setSelectedNodeKey).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Prompt 预览')).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()

    // 第二下 Esc：详情态才关抽屉。
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(setSelectedNodeKey).toHaveBeenCalledWith(null)
  })

  it('#770 单一关闭出口：详情态与预览态各只有一个「关闭节点配置」，预览态的返回只在左侧返回条', () => {
    renderDrawer()
    expect(
      screen.getAllByRole('button', { name: '关闭节点配置' })
    ).toHaveLength(1)
    expect(screen.queryByRole('button', { name: '返回节点详情' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))
    expect(
      screen.getAllByRole('button', { name: '关闭节点配置' })
    ).toHaveLength(1)
    expect(
      screen.getAllByRole('button', { name: '返回节点详情' })
    ).toHaveLength(1)
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
    useSettingStore.setState({ workspaceId: 'ws1' })
    mockApi.mockResolvedValue({})
  })

  // #1079（#440 P3b）：Agent 区块只编辑节点执行档案——即使目录命中同
  // capability 的 published Agent，也不内嵌 Agent 编辑器 / 汇总卡。
  it('edits the node profile without any Agent definition editor', () => {
    render(bodyFor('generate_key_info'))

    expect(screen.getByLabelText('Runtime')).toBeInTheDocument()
    expect(screen.queryByText('agent-key-info')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Agent ID')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /发布|新建 Agent/ })
    ).not.toBeInTheDocument()
  })

  it('asks a legacy node only for a runtime (no Agent editor fallback)', () => {
    const legacyYaml = definitionYaml.replace('execution:\n  runtime: pi\n', '')
    render(bodyFor('review', legacyYaml))

    expect(screen.getByText(/尚未内联执行档案/)).toBeInTheDocument()
    expect(screen.getByLabelText('Runtime')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '查看 Prompt' })
    ).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Agent ID')).not.toBeInTheDocument()
  })
})
