import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api'
import { getSkillDetail } from '../../../api/agentCatalogApi'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { WorkflowNodeDetailView } from './WorkflowNodeDetailView'

// inspector 各 section（code/config/agent 执行详情）统一走 '../../api' 的 api。
vi.mock('../../../api', () => ({ api: vi.fn() }))
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
  '  review:',
  '    type: agent',
  '    capability: review',
  '',
].join('\n')

function renderView(
  onBack: () => void = () => {},
  nodeKey = 'generate_key_info'
) {
  return render(
    <TestQueryProvider>
      <WorkflowNodeDetailView
        workflow={workflow}
        nodeKey={nodeKey}
        agentCatalog={agentCatalog}
        agentBindingStatus="ready"
        definitionYaml={definitionYaml}
        setDefinitionYaml={() => {}}
        readOnly={false}
        agentOpen={false}
        onToggleAgent={() => {}}
        onBack={onBack}
      />
    </TestQueryProvider>
  )
}

// rerender 用的纯元素工厂（默认参数与 renderView 一致；catalog 可覆盖——
// P1 用例需要两个 capability 都未绑定 Agent 的场景）。
function viewFor(nodeKey: string, catalog: AgentDefinition[] = agentCatalog) {
  return (
    <TestQueryProvider>
      <WorkflowNodeDetailView
        workflow={workflow}
        nodeKey={nodeKey}
        agentCatalog={catalog}
        agentBindingStatus="ready"
        definitionYaml={definitionYaml}
        setDefinitionYaml={() => {}}
        readOnly={false}
        agentOpen={false}
        onToggleAgent={() => {}}
        onBack={() => {}}
      />
    </TestQueryProvider>
  )
}

describe('WorkflowNodeDetailView', () => {
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

  it('shows the plain breadcrumb and backs out to the DAG by default', () => {
    const onBack = vi.fn()
    renderView(onBack)

    expect(screen.getByText('Demo DAG / 生成关键信息')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回 DAG' }))
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('deepens the breadcrumb in the prompt preview and the back button steps back to the node details', () => {
    const onBack = vi.fn()
    renderView(onBack)

    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))

    expect(
      screen.getByText('Demo DAG / 生成关键信息 / Prompt')
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Prompt 预览')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回节点详情' }))
    expect(onBack).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Prompt 预览')).not.toBeInTheDocument()
    expect(screen.getByText('Demo DAG / 生成关键信息')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回 DAG' }))
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('deepens the breadcrumb in the skill preview', async () => {
    renderView()

    fireEvent.click(screen.getByRole('button', { name: '浏览技能文件' }))

    expect(
      screen.getByText('Demo DAG / 生成关键信息 / 技能文件')
    ).toBeInTheDocument()
    expect(await screen.findByText('# Skill')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回节点详情' }))
    expect(screen.queryByLabelText('技能文件预览')).not.toBeInTheDocument()
    expect(screen.getByText('Demo DAG / 生成关键信息')).toBeInTheDocument()
  })

  // #409：Agent 区块结构简化——无「编辑 Agent」开合按钮，编辑面板默认
  // 内联展开；可编辑态不再渲染重复的只读汇总卡片（agent id 汇总行）。
  it('renders the agent editor inline without a toggle button or summary card', () => {
    renderView()

    expect(
      screen.queryByRole('button', { name: '编辑 Agent' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '为此 capability 新建 Agent' })
    ).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-editor-stub')).toBeInTheDocument()
    expect(screen.queryByText('agent-key-info')).not.toBeInTheDocument()
  })

  it('resets the preview when the selected node changes', () => {
    const { rerender } = renderView()
    fireEvent.click(screen.getByRole('button', { name: '查看 Prompt' }))
    expect(
      screen.getByText('Demo DAG / 生成关键信息 / Prompt')
    ).toBeInTheDocument()

    const viewFor = (key: string) => (
      <TestQueryProvider>
        <WorkflowNodeDetailView
          workflow={workflow}
          nodeKey={key}
          agentCatalog={agentCatalog}
          agentBindingStatus="ready"
          definitionYaml={definitionYaml}
          setDefinitionYaml={() => {}}
          readOnly={false}
          agentOpen={false}
          onToggleAgent={() => {}}
          onBack={() => {}}
        />
      </TestQueryProvider>
    )

    rerender(viewFor('review'))

    // nodeKey 变化即真正清除预览：切走后落在节点详情。
    expect(screen.queryByLabelText('Prompt 预览')).not.toBeInTheDocument()
    expect(screen.getByText('Demo DAG / 评审')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '返回 DAG' })).toBeInTheDocument()

    // 切回原节点也不恢复预览（仍是节点详情，不是挂起态）。
    rerender(viewFor('generate_key_info'))
    expect(screen.queryByLabelText('Prompt 预览')).not.toBeInTheDocument()
    expect(screen.getByText('Demo DAG / 生成关键信息')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '返回 DAG' })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()
  })

  // #426 review P1：未绑定 Agent 的节点上创建草稿后切到另一个未绑定节点
  // （agentId 仍为 null，React 复用同一面板实例）——面板必须按新节点的
  // capability 全新挂载，不能带着前一个节点面板里的 createdAgentId 继续编辑
  // 前一个 Agent（#409 去掉开合按钮后已无「收起重置」的兜底入口）。
  it('remounts the inline agent editor fresh when switching to a node with a different capability', () => {
    // 两个节点都不绑定 Agent（空目录），对齐 review 场景：agentId 均为
    // null，React 会复用同一面板实例——正是 P1 的暴露条件。
    const { rerender } = render(viewFor('generate_key_info', []))

    // 节点 A（generate_key_info）：capability 预填的新建表单。
    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-mount',
      '1'
    )
    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-initial-capability',
      'generate_key_info'
    )

    // 切到节点 B（review，同样未绑定，agentId 仍为 null）：面板重挂（挂载
    // 序号 +1）、capability 换成 B 的——A 里创建的草稿身份不会渗到 B。
    rerender(viewFor('review', []))

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
    const { rerender } = renderView()

    expect(screen.getByTestId('agent-editor-stub')).toHaveAttribute(
      'data-mount',
      '1'
    )

    rerender(viewFor('generate_key_info'))

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
      <TestQueryProvider>
        <WorkflowNodeDetailView
          workflow={workflow}
          nodeKey="generate_key_info"
          agentCatalog={[]}
          agentBindingStatus="pending"
          definitionYaml={definitionYaml}
          setDefinitionYaml={() => {}}
          readOnly={false}
          agentOpen={false}
          onToggleAgent={() => {}}
          onBack={() => {}}
        />
      </TestQueryProvider>
    )

    expect(screen.getByText('Agent 绑定解析中...')).toBeInTheDocument()
    expect(screen.queryByTestId('agent-editor-stub')).not.toBeInTheDocument()
    // 两个未绑定 capability 的节点间切换：pending 期间同样不出表单。
    expect(screen.queryByLabelText('Agent ID')).not.toBeInTheDocument()
  })

  // #426 review P2：查询失败与「确认未绑定」必须区分——失败时显示错误提示
  // （顶部有全局重试横幅），不退回可操作表单（否则失败场景回到 P2）。
  it('renders an error placeholder instead of the create form when the binding query failed', () => {
    render(
      <TestQueryProvider>
        <WorkflowNodeDetailView
          workflow={workflow}
          nodeKey="generate_key_info"
          agentCatalog={[]}
          agentBindingStatus="error"
          definitionYaml={definitionYaml}
          setDefinitionYaml={() => {}}
          readOnly={false}
          agentOpen={false}
          onToggleAgent={() => {}}
          onBack={() => {}}
        />
      </TestQueryProvider>
    )

    expect(screen.getByText('Agent 目录加载失败')).toBeInTheDocument()
    expect(screen.queryByTestId('agent-editor-stub')).not.toBeInTheDocument()
  })
})
