import { fireEvent, render, screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'

// #1079（#440 P3b）：节点详情不再解析 capability → Agent 定义，Agent 目录 /
// 定义 API 一律不应被调用（mock 成抛错，误调即测试失败）。
vi.mock('../../../api/agentCatalogApi', () => ({
  getAgentCatalog: vi.fn(() => {
    throw new Error('agent catalog must not be fetched by the section')
  }),
}))
vi.mock('../../../api/agentDefinitions', () => ({
  fetchAgentDefinitions: vi.fn(() => {
    throw new Error('agent definitions must not be fetched by the section')
  }),
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

// 自含节点（节点级 execution.runtime）+ workflow 顶层执行默认。
const selfContainedYaml = `execution:\n  provider: deepseek\n  model: your-model-b\n  thinking: low\nnodes:\n  generate_key_info:\n    type: agent\n    capability: generate_key_info\n    execution:\n      runtime: pi\n`

// legacy 节点：节点与 workflow 顶层都没有 runtime（v93 未能内联）。
const legacyYaml = `execution:\n  provider: deepseek\nnodes:\n  generate_key_info:\n    capability: generate_key_info\n`

function renderSection(
  props: React.ComponentProps<typeof WorkflowNodeExecutionSection>
) {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1/studio']}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/studio"
          element={<WorkflowNodeExecutionSection {...props} />}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('WorkflowNodeExecutionSection node profile (#935 / #1079)', () => {
  // 节点 skill 编辑行按 settingStore 的 workspace 渲染（无 workspace 时隐藏）。
  beforeEach(() => {
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('edits a self-contained node profile in place', () => {
    renderSection({
      node,
      definitionYaml: selfContainedYaml,
      setDefinitionYaml: () => {},
    })

    expect(screen.queryByText(/尚未内联执行档案/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Runtime')).toBeInTheDocument()
    expect(screen.getByTestId('skill-selector-stub')).toBeInTheDocument()
    expect(screen.getByLabelText('Model')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '查看 Prompt' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '浏览技能文件' })
    ).toBeInTheDocument()
    // tools 未声明时兜底是 runtime 默认档，不再提「Agent 默认」。
    expect(screen.queryByText(/Agent 默认/)).not.toBeInTheDocument()
  })

  it('treats the workflow top-level runtime as a self-contained profile', () => {
    renderSection({
      node,
      definitionYaml: `execution:\n  runtime: pi\n  provider: deepseek\nnodes:\n  generate_key_info:\n    capability: generate_key_info\n`,
      setDefinitionYaml: () => {},
    })

    expect(screen.queryByText(/尚未内联执行档案/)).not.toBeInTheDocument()
    expect(screen.getByText(/继承 workflow 默认（pi）/)).toBeInTheDocument()
  })

  it('writes the selected runtime onto the node execution block', () => {
    let nextYaml = ''
    renderSection({
      node,
      definitionYaml: selfContainedYaml,
      setDefinitionYaml: (value) => {
        nextYaml = value
      },
    })

    fireEvent.mouseDown(screen.getByLabelText('Runtime'))
    fireEvent.click(screen.getByRole('option', { name: 'velites' }))

    expect(nextYaml).toContain('runtime: velites')
  })

  it('writes a node model override to workflow YAML', () => {
    let nextYaml = ''
    renderSection({
      node,
      definitionYaml: selfContainedYaml,
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
        runtime: 'pi',
        provider: 'deepseek',
        model: '',
        thinking: '',
        prompt: '',
        prompt_mode: '',
      },
    }
    const initialYaml = `execution:\n  provider: deepseek\nnodes:\n  generate_key_info:\n    capability: generate_key_info\n    execution:\n      runtime: pi\n      provider: deepseek\n`
    const { rerender } = renderSection({
      node: nodeWithProvider,
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
              <WorkflowNodeExecutionSection
                node={nodeWithProvider}
                definitionYaml={nextYaml}
                setDefinitionYaml={(value) => {
                  nextYaml = value
                }}
              />
            }
          />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByLabelText('Provider')).toHaveValue('')
    expect(screen.getByText('继承 workflow 默认：deepseek')).toBeInTheDocument()
  })

  it('offers datalist options from the runtime models of online workers', () => {
    renderSection({
      node,
      definitionYaml: selfContainedYaml,
      setDefinitionYaml: () => {},
    })

    const providerInput = screen.getByLabelText('Provider') as HTMLInputElement
    const providerList = document.getElementById(
      providerInput.getAttribute('list')!
    ) as HTMLDataListElement
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
    renderSection({
      node,
      definitionYaml: selfContainedYaml,
      setDefinitionYaml: () => {},
    })

    const thinkingSelect = screen.getByLabelText(
      'Thinking'
    ) as HTMLSelectElement
    expect(thinkingSelect.options[0].textContent).toBe(
      '继承 workflow 默认（low）'
    )
  })

  // #1079（#440 P3b）：legacy 节点只提示补 runtime + runtime 下拉——不再
  // 内嵌 AgentEditor / Agent 定义汇总卡，也不渲染 skill / 执行参数编辑
  // （补上 runtime 后节点即自含，切到上方的执行档案编辑区）。
  it('only asks a legacy node for a runtime', () => {
    renderSection({
      node,
      definitionYaml: legacyYaml,
      setDefinitionYaml: () => {},
    })

    expect(screen.getByText(/尚未内联执行档案/)).toBeInTheDocument()
    expect(screen.getByLabelText('Runtime')).toBeInTheDocument()
    expect(screen.queryByTestId('skill-selector-stub')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Model')).not.toBeInTheDocument()
    expect(screen.queryByText(/published Agent/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Agent ID')).not.toBeInTheDocument()
  })

  it('makes a legacy node self-contained once a runtime is picked', () => {
    let nextYaml = ''
    renderSection({
      node,
      definitionYaml: legacyYaml,
      setDefinitionYaml: (value) => {
        nextYaml = value
      },
    })

    fireEvent.mouseDown(screen.getByLabelText('Runtime'))
    fireEvent.click(screen.getByRole('option', { name: 'velites' }))

    expect(nextYaml).toContain('runtime: velites')
  })

  it('shows the code-pool state without any agent entry for a code node (#392)', () => {
    renderSection({
      node: { ...node, node_type: 'code', capability: 'missing' },
      definitionYaml: selfContainedYaml,
      setDefinitionYaml: () => {},
    })

    expect(screen.getByText('内置 code 池执行')).toBeInTheDocument()
    expect(screen.queryByLabelText('Runtime')).not.toBeInTheDocument()
    expect(screen.queryByTestId('skill-selector-stub')).not.toBeInTheDocument()
  })

  it('renders nothing for approval nodes (#392 Phase 2: registry gates the section)', () => {
    const { container } = renderSection({
      node: { ...node, node_type: 'approval', capability: '' },
      definitionYaml: selfContainedYaml,
      setDefinitionYaml: () => {},
    })

    // 审批门由 WorkflowNodeApprovalConfigSection 承载；本 section 挂在
    // code/agent 类型（nodeTypeSections 注册表），直接喂 approval 渲染空。
    expect(container).toBeEmptyDOMElement()
  })
})
