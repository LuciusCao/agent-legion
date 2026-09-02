import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'
import { useSettingStore } from '../../../stores/settingStore'
import { fetchAgentDefinitions } from '../../../api/agentDefinitions'
import type { AgentListResponse, WorkflowNodeRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { StudioNav } from '../shared/workflowStudioNav'
import { StudioNavContext } from '../shared/useStudioNavState'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'

vi.mock('../../../api/agentCatalogApi', () => ({
  getAgentCatalog: vi.fn().mockResolvedValue({ agents: [] }),
}))

// #387：draft-only Agent 的节点解析回落 agent-definitions（含 draft）。
vi.mock('../../../api/agentDefinitions', () => ({
  fetchAgentDefinitions: vi.fn(),
}))
vi.mock('../../../stores/settingStore', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../stores/settingStore')>()
  return actual
})

// 内嵌编辑器的完整行为由 WorkflowNodeAgentEditor.test.tsx 覆盖。
vi.mock('./AgentEditor', () => ({
  AgentEditor: () => <div data-testid="agent-editor-stub" />,
}))

// 节点 skill 编辑行的交互由 WorkflowNodeSkillEditor.test.tsx 覆盖；此处 stub
// 掉带真实 API 的 SkillSelector，只验证 section 的渲染分发。
vi.mock('../../../components/SkillSelector', () => ({
  SkillSelector: () => <div data-testid="skill-selector-stub" />,
}))

// 「继承默认」提示来自草稿 YAML 顶层 execution 块；datalist 选项来自
// useWorkspaceRuntimeModels（在线 Worker 声明的 runtime/provider/model）。
vi.mock('../shared/useWorkspaceRuntimeModels', () => ({
  useWorkspaceRuntimeModels: () => ({
    data: {
      runtimes: {
        pi: { deepseek: ['your-model-b', 'your-model-c'] },
      },
    },
  }),
}))

const node: WorkflowNodeRecord = {
  key: 'generate_key_info',
  label: '生成关键信息',
  capability: 'generate_key_info',
  // 显式 Agent 节点（#284）：类型判定只读 node_type，不再按 capability 反推。
  node_type: 'agent',
  after: [],
  inputs: [],
  outputs: [],
  terminal: null,
}

const agentCatalog: AgentDefinition[] = [
  {
    id: 'question-key-info-v1',
    runtime: 'pi',
    capability: 'generate_key_info',
    skill: 'demo_workflow/generate_key_info',
    tools: ['read', 'write', 'bash'],
    requires_labels: {},
    provider: 'deepseek',
    model: 'your-model-b',
    thinking: 'low',
    skill_ref: 'v1.3.8',
    skill_commit: '5c5eae72064abde37bfc4b07a4b2f7e9637c473d',
  },
]

const editorProps = {
  definitionYaml: `execution:\n  provider: deepseek\n  model: your-model-b\n  thinking: low\nnodes:\n  generate_key_info:\n    capability: generate_key_info\n`,
  setDefinitionYaml: () => {},
  agentCatalog,
}

// 组件经 useStudioNav 读 openAgent 的目标草稿身份：默认无 pending。
const navStub: StudioNav = {
  openAgent: () => {},
  pendingAgentId: null,
  clearPendingAgentId: () => {},
}

function renderSection(
  props: React.ComponentProps<typeof WorkflowNodeExecutionSection>,
  nav: StudioNav = navStub
) {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1/studio']}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/studio"
          element={
            <StudioNavContext.Provider value={nav}>
              <WorkflowNodeExecutionSection {...props} />
            </StudioNavContext.Provider>
          }
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('WorkflowNodeExecutionSection', () => {
  beforeEach(() => {
    useSettingStore.setState({ workspaceId: 'ws1' })
    vi.mocked(fetchAgentDefinitions).mockResolvedValue({ agents: [] })
  })

  it('shows the executor binding for the selected node capability', () => {
    renderSection({ node, ...editorProps })

    expect(screen.getByText('question-key-info-v1')).toBeInTheDocument()
    expect(screen.getByText('pi')).toBeInTheDocument()
    expect(
      screen.getByText('demo_workflow/generate_key_info')
    ).toBeInTheDocument()
    expect(screen.getByText('read, write, bash')).toBeInTheDocument()
    expect(screen.getByText('v1.3.8 · 5c5eae7')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '浏览技能文件' })
    ).toBeInTheDocument()
    expect(screen.getByText(/your-model-b/)).toBeInTheDocument()
    expect(screen.queryByText('code-default')).not.toBeInTheDocument()
  })

  it('writes a node model override to workflow YAML', () => {
    let nextYaml = ''
    renderSection({
      node,
      agentCatalog,
      definitionYaml: editorProps.definitionYaml,
      setDefinitionYaml: (value) => {
        nextYaml = value
      },
    })

    fireEvent.change(screen.getByLabelText('Model'), {
      target: { value: 'gpt-5' },
    })

    expect(nextYaml).toContain('model: gpt-5')
  })

  it('keeps a cleared provider empty instead of restoring the persisted value', () => {
    let nextYaml = ''
    const nodeWithProvider: WorkflowNodeRecord = {
      ...node,
      execution: {
        provider: 'deepseek',
        model: '',
        thinking: '',
        prompt: '',
      },
    }
    const initialYaml = `execution:\n  provider: deepseek\nnodes:\n  generate_key_info:\n    capability: generate_key_info\n    execution:\n      provider: deepseek\n`
    const { rerender } = renderSection({
      node: nodeWithProvider,
      agentCatalog,
      definitionYaml: initialYaml,
      setDefinitionYaml: (value) => {
        nextYaml = value
      },
    })

    fireEvent.change(screen.getByLabelText('Provider'), {
      target: { value: '' },
    })

    // 顶层 execution 默认保留在 YAML，节点级 provider（6 空格缩进）必须被移除。
    expect(nextYaml).not.toContain('      provider:')
    rerender(
      <MemoryRouter initialEntries={['/workspaces/ws1/studio']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/studio"
            element={
              <StudioNavContext.Provider value={navStub}>
                <WorkflowNodeExecutionSection
                  node={nodeWithProvider}
                  agentCatalog={agentCatalog}
                  definitionYaml={nextYaml}
                  setDefinitionYaml={(value) => {
                    nextYaml = value
                  }}
                />
              </StudioNavContext.Provider>
            }
          />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByLabelText('Provider')).toHaveValue('')
    expect(screen.getByText('继承 workflow 默认：deepseek')).toBeInTheDocument()
  })

  it('offers datalist options from the runtime models of online workers', () => {
    renderSection({ node, ...editorProps })

    const providerInput = screen.getByLabelText('Provider') as HTMLInputElement
    const providerList = document.getElementById(
      providerInput.getAttribute('list')!
    ) as HTMLDataListElement
    expect(providerList).not.toBeNull()
    expect(
      Array.from(providerList.options).map((option) => option.value)
    ).toEqual(['deepseek'])

    // Model 选项跟随当前 provider 之外的回退：未填 provider 时给全部型号。
    const modelInput = screen.getByLabelText('Model') as HTMLInputElement
    const modelList = document.getElementById(
      modelInput.getAttribute('list')!
    ) as HTMLDataListElement
    expect(Array.from(modelList.options).map((option) => option.value)).toEqual(
      ['your-model-b', 'your-model-c']
    )
  })

  it('shows the workflow thinking default on the empty option', () => {
    renderSection({ node, ...editorProps })

    const thinkingSelect = screen.getByLabelText(
      'Thinking'
    ) as HTMLSelectElement
    expect(thinkingSelect.options[0].textContent).toBe(
      '继承 workflow 默认（low）'
    )
  })

  it('shows the code-pool state and the switch-to-agent entry for a code node', () => {
    renderSection({
      node: { ...node, node_type: 'code', capability: 'missing' },
      ...editorProps,
    })

    expect(screen.getByText('内置 code 池执行')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '切换为 Agent 执行' })
    ).toBeInTheDocument()
  })

  it('points an agent node without a published Agent to the create entry', () => {
    renderSection({
      node: { ...node, node_type: 'agent', capability: 'missing' },
      ...editorProps,
    })

    expect(screen.getByText(/暂无 published Agent/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '为此 capability 新建 Agent' })
    ).toBeInTheDocument()
  })

  // #387：MCP 建的 draft-only Agent 不在 published 目录里，但节点详情要能
  // 解析到它（编辑/发布入口可达），并明确提示「未发布」。
  it('resolves a draft-only agent from agent-definitions and flags it unpublished', async () => {
    vi.mocked(fetchAgentDefinitions).mockResolvedValue({
      agents: [
        {
          agent_id: 'draft-agent',
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: 'demo_workflow/generate_key_info',
          version: 1,
          status: 'draft',
          has_draft: true,
          published_at: null,
        },
      ],
    })
    renderSection({ node, ...editorProps, agentCatalog: [] })

    expect(await screen.findByText('draft-agent')).toBeInTheDocument()
    expect(screen.getByText(/草稿 Agent 未发布/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '编辑 Agent' })
    ).toBeInTheDocument()
  })

  it('prefers the published catalog agent over a same-capability draft', async () => {
    vi.mocked(fetchAgentDefinitions).mockResolvedValue({
      agents: [
        {
          agent_id: 'draft-agent',
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: '',
          version: 1,
          status: 'draft',
          has_draft: true,
          published_at: null,
        },
      ],
    })
    renderSection({ node, ...editorProps })

    // published 版本渲染后草稿提示不出现。
    await waitFor(() =>
      expect(screen.getByText('question-key-info-v1')).toBeInTheDocument()
    )
    expect(screen.queryByText(/草稿 Agent 未发布/)).not.toBeInTheDocument()
  })

  // codex P1 on #391：同 capability 允许存在多个未发布草稿（服务端只在
  // publish 时校验冲突）——保留 openAgent 点击的草稿身份，不总取第一个。
  it('honors the pending agent id when several drafts share the capability', async () => {
    vi.mocked(fetchAgentDefinitions).mockResolvedValue({
      agents: [
        {
          agent_id: 'first-draft',
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: '',
          version: 1,
          status: 'draft',
          has_draft: true,
          published_at: null,
        },
        {
          agent_id: 'clicked-draft',
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: '',
          version: 1,
          status: 'draft',
          has_draft: true,
          published_at: null,
        },
      ],
    })
    const clearPending = vi.fn()
    renderSection(
      { node, ...editorProps, agentCatalog: [] },
      {
        ...navStub,
        pendingAgentId: 'clicked-draft',
        clearPendingAgentId: clearPending,
      }
    )

    // 命中点击的草稿（而非列表第一个），解析后清除 pending。
    expect(await screen.findByText('clicked-draft')).toBeInTheDocument()
    expect(screen.queryByText('first-draft')).not.toBeInTheDocument()
    await waitFor(() => expect(clearPending).toHaveBeenCalled())
  })

  // subagent review P2-1 on #391：pending 的清除绑定「数据 settle + 命中
  // 确认」——列表还在加载（缓存滞后于 turn_end 失效重取）时保留 pending，
  // 不能在未命中的首次渲染就清掉导致身份丢失、回落到列表第一个。
  it('keeps the pending agent id while the definitions query is still loading', async () => {
    let resolveDefinitions: (value: AgentListResponse) => void = () => {}
    vi.mocked(fetchAgentDefinitions).mockReturnValue(
      new Promise((resolve) => {
        resolveDefinitions = resolve
      }) as ReturnType<typeof fetchAgentDefinitions>
    )
    const clearPending = vi.fn()
    renderSection(
      { node, ...editorProps, agentCatalog: [] },
      {
        ...navStub,
        pendingAgentId: 'clicked-draft',
        clearPendingAgentId: clearPending,
      }
    )

    // 未 settle：pending 不清除，草稿解析暂缺（显示暂无指引）。
    expect(screen.getByText(/暂无 published Agent/)).toBeInTheDocument()
    expect(clearPending).not.toHaveBeenCalled()

    resolveDefinitions({
      agents: [
        {
          agent_id: 'first-draft',
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: '',
          version: 1,
          status: 'draft',
          has_draft: true,
          published_at: null,
        },
        {
          agent_id: 'clicked-draft',
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: '',
          version: 1,
          status: 'draft',
          has_draft: true,
          published_at: null,
        },
      ],
    })

    // settle 后命中点击的草稿并清除 pending。
    expect(await screen.findByText('clicked-draft')).toBeInTheDocument()
    expect(screen.queryByText('first-draft')).not.toBeInTheDocument()
    await waitFor(() => expect(clearPending).toHaveBeenCalled())
  })

  it('renders the node skill editor for agent-routed nodes only', () => {
    const { unmount } = renderSection({ node, ...editorProps })

    // Agent 路由节点：skill 编辑行（key 选择 + ref 输入）。
    expect(screen.getByTestId('skill-selector-stub')).toBeInTheDocument()
    expect(screen.getByLabelText('Skill ref')).toBeInTheDocument()
    unmount()

    renderSection({
      node: { ...node, node_type: 'code' },
      ...editorProps,
    })
    expect(screen.queryByTestId('skill-selector-stub')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Skill ref')).not.toBeInTheDocument()
  })

  it('omits the skill row and version line when the definition has no skill', () => {
    const skillless: AgentDefinition[] = [
      {
        ...agentCatalog[0],
        skill: '',
        skill_ref: null,
        skill_commit: null,
      },
    ]
    renderSection({ node, ...editorProps, agentCatalog: skillless })

    expect(screen.getByText('question-key-info-v1')).toBeInTheDocument()
    expect(screen.queryByText('Skill')).not.toBeInTheDocument()
    expect(screen.queryByText(/5c5eae7/)).not.toBeInTheDocument()
  })

  it('shows the approval-gate hint and hides the agent editor for approval nodes', () => {
    renderSection({
      node: { ...node, node_type: 'approval', capability: '' },
      ...editorProps,
    })

    expect(screen.getByText('审批门')).toBeInTheDocument()
    expect(screen.getByText(/awaiting_approval/)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '切换为 Agent 执行' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '为此 capability 新建 Agent' })
    ).not.toBeInTheDocument()
  })

  it('toggles the embedded agent editor for the bound agent', () => {
    renderSection({ node, ...editorProps })

    expect(screen.queryByTestId('agent-editor-stub')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '编辑 Agent' }))
    expect(screen.getByTestId('agent-editor-stub')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '收起 Agent 编辑' }))
    expect(screen.queryByTestId('agent-editor-stub')).not.toBeInTheDocument()
  })

  it('hides the agent edit and create entries in read-only mode', () => {
    renderSection({ node, ...editorProps, readOnly: true })

    expect(
      screen.queryByRole('button', { name: '编辑 Agent' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '为此 capability 新建 Agent' })
    ).not.toBeInTheDocument()
  })
})
