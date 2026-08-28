import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import type { AgentDefinition } from '../../../types/executorTypes'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'

vi.mock('../../../api/executorApi', () => ({
  getExecutorCatalog: vi.fn().mockResolvedValue({ agents: [] }),
}))

// 内嵌编辑器的完整行为由 WorkflowNodeAgentEditor.test.tsx 覆盖。
vi.mock('./AgentEditor', () => ({
  AgentEditor: () => <div data-testid="agent-editor-stub" />,
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
  workflowKey: 'demo-wf',
}

function renderSection(
  props: React.ComponentProps<typeof WorkflowNodeExecutionSection>
) {
  return render(
    <TestQueryProvider>
      {<WorkflowNodeExecutionSection {...props} />}
    </TestQueryProvider>
  )
}

describe('WorkflowNodeExecutionSection', () => {
  beforeEach(() => {
    useSettingStore.setState({ workspaceId: 'ws1' })
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
      workflowKey: 'demo-wf',
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
      workflowKey: 'demo-wf',
    })

    fireEvent.change(screen.getByLabelText('Provider'), {
      target: { value: '' },
    })

    // 顶层 execution 默认保留在 YAML，节点级 provider（6 空格缩进）必须被移除。
    expect(nextYaml).not.toContain('      provider:')
    rerender(
      <TestQueryProvider>
        <WorkflowNodeExecutionSection
          node={nodeWithProvider}
          agentCatalog={agentCatalog}
          definitionYaml={nextYaml}
          setDefinitionYaml={(value) => {
            nextYaml = value
          }}
          workflowKey="demo-wf"
        />
      </TestQueryProvider>
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

  it('shows the code-pool state and the create-agent entry when no agent routes the capability', () => {
    renderSection({ node: { ...node, capability: 'missing' }, ...editorProps })

    expect(screen.getByText('内置 code 池执行')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '为此 capability 新建 Agent' })
    ).toBeInTheDocument()
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
