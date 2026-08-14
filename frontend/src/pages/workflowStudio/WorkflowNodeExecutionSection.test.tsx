import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../testing/testQueryClient'
import type {
  AgentDefinition,
  ExecutorDefinition,
} from '../../types/executorTypes'
import type { WorkflowNodeRecord } from '../../types'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'
import { StudioNavContext } from './workflowStudioNav'

vi.mock('../../api/executorApi', () => ({
  getExecutorCatalog: vi.fn().mockResolvedValue({ executors: [], agents: [] }),
}))

// 「继承默认」提示来自 workspace settings 的 agentDefaults（hook 拉取），
// 不再读 executor catalog 的 agent 条目。
vi.mock('./useWorkspaceAgentDefaults', () => ({
  useWorkspaceAgentDefaults: () => ({
    provider: 'deepseek',
    model: 'your-model-b',
    thinking: 'low',
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

const executorCatalog: ExecutorDefinition[] = [
  {
    id: 'code-default',
    kind: 'code',
    global_capacity: 16,
    capabilities: ['fetch_questions'],
    capability_details: [
      {
        name: 'fetch_questions',
        path: 'workflow_nodes/fetch_questions.py',
      },
    ],
  },
]

const agentCatalog: AgentDefinition[] = [
  {
    id: 'question-key-info-v1',
    runtime: 'pi',
    capability: 'generate_key_info',
    skill: 'question_comprehension_info/generate_key_info',
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
  definitionYaml: `nodes:\n  generate_key_info:\n    capability: generate_key_info\n`,
  setDefinitionYaml: () => {},
  agentCatalog,
  workflowKey: 'demo-wf',
}

describe('WorkflowNodeExecutionSection', () => {
  it('shows the executor binding for the selected node capability', () => {
    render(
      <WorkflowNodeExecutionSection
        node={node}
        executorCatalog={executorCatalog}
        {...editorProps}
      />
    )

    expect(screen.getByText('question-key-info-v1')).toBeInTheDocument()
    expect(screen.getByText('pi')).toBeInTheDocument()
    expect(
      screen.getByText('question_comprehension_info/generate_key_info')
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
    render(
      <WorkflowNodeExecutionSection
        node={node}
        executorCatalog={executorCatalog}
        agentCatalog={agentCatalog}
        definitionYaml={editorProps.definitionYaml}
        setDefinitionYaml={(value) => {
          nextYaml = value
        }}
        workflowKey="demo-wf"
      />
    )

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
    const initialYaml = `nodes:\n  generate_key_info:\n    capability: generate_key_info\n    execution:\n      provider: deepseek\n`
    const { rerender } = render(
      <WorkflowNodeExecutionSection
        node={nodeWithProvider}
        executorCatalog={executorCatalog}
        agentCatalog={agentCatalog}
        definitionYaml={initialYaml}
        setDefinitionYaml={(value) => {
          nextYaml = value
        }}
        workflowKey="demo-wf"
      />
    )

    fireEvent.change(screen.getByLabelText('Provider'), {
      target: { value: '' },
    })

    expect(nextYaml).not.toContain('provider:')
    rerender(
      <WorkflowNodeExecutionSection
        node={nodeWithProvider}
        executorCatalog={executorCatalog}
        agentCatalog={agentCatalog}
        definitionYaml={nextYaml}
        setDefinitionYaml={(value) => {
          nextYaml = value
        }}
        workflowKey="demo-wf"
      />
    )
    expect(screen.getByLabelText('Provider')).toHaveValue('')
    expect(screen.getByText('继承全局：deepseek')).toBeInTheDocument()
  })

  it('shows an empty state when no executor supports the capability', () => {
    render(
      <TestQueryProvider>
        <WorkflowNodeExecutionSection
          node={{ ...node, capability: 'missing' }}
          executorCatalog={executorCatalog}
          {...editorProps}
        />
      </TestQueryProvider>
    )

    expect(screen.getByText('未匹配到 executor capability')).toBeInTheDocument()
  })

  it('jumps to the agent editor from the agent card', () => {
    const openAgent = vi.fn()
    render(
      <StudioNavContext.Provider value={{ openAgent, openExecutor: () => {} }}>
        <WorkflowNodeExecutionSection
          node={node}
          executorCatalog={executorCatalog}
          {...editorProps}
        />
      </StudioNavContext.Provider>
    )

    fireEvent.click(screen.getByRole('button', { name: '在 Agent 管理中打开' }))

    expect(openAgent).toHaveBeenCalledWith('question-key-info-v1')
  })
})
