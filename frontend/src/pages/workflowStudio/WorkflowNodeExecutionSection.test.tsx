import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ExecutorDefinition } from '../../types/executorTypes'
import type { WorkflowNodeRecord } from '../../types'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'

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
    id: 'local-default',
    kind: 'local',
    global_capacity: 16,
    capabilities: ['fetch_questions'],
    capability_details: [
      {
        name: 'fetch_questions',
        handler: 'question_comprehension_info.fetch_questions',
      },
    ],
  },
  {
    id: 'pi',
    kind: 'pi',
    global_capacity: 12,
    capabilities: ['generate_key_info'],
    capability_details: [
      {
        name: 'generate_key_info',
        skill: 'question_comprehension_info/generate_key_info',
        tools: ['read', 'write', 'bash'],
        provider: 'deepseek',
        model: 'your-model-b',
        thinking: 'low',
        skill_ref: 'v1.3.8',
        skill_commit: '5c5eae72064abde37bfc4b07a4b2f7e9637c473d',
      },
    ],
  },
]

const editorProps = {
  definitionYaml: `nodes:\n  generate_key_info:\n    capability: generate_key_info\n`,
  setDefinitionYaml: () => {},
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

    expect(screen.getAllByText('pi')).toHaveLength(1)
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
    expect(screen.queryByText('local-default')).not.toBeInTheDocument()
  })

  it('writes a node model override to workflow YAML', () => {
    let nextYaml = ''
    render(
      <WorkflowNodeExecutionSection
        node={node}
        executorCatalog={executorCatalog}
        definitionYaml={editorProps.definitionYaml}
        setDefinitionYaml={(value) => {
          nextYaml = value
        }}
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
        definitionYaml={initialYaml}
        setDefinitionYaml={(value) => {
          nextYaml = value
        }}
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
        definitionYaml={nextYaml}
        setDefinitionYaml={(value) => {
          nextYaml = value
        }}
      />
    )
    expect(screen.getByLabelText('Provider')).toHaveValue('')
    expect(screen.getByText('继承全局：deepseek')).toBeInTheDocument()
  })

  it('shows an empty state when no executor supports the capability', () => {
    render(
      <WorkflowNodeExecutionSection
        node={{ ...node, capability: 'missing' }}
        executorCatalog={executorCatalog}
        {...editorProps}
      />
    )

    expect(screen.getByText('未匹配到 executor capability')).toBeInTheDocument()
  })

  it('shows the agent summary for nodes with a declared concurrency cap', () => {
    render(
      <WorkflowNodeExecutionSection
        node={{ ...node, max_concurrency: 20 }}
        executorCatalog={executorCatalog}
        {...editorProps}
      />
    )

    const summary = screen.getByTestId('agent-node-summary')
    expect(summary.textContent).toContain('generate_key_info')
    expect(summary.textContent).toContain('并发上限为 workspace 级')
  })

  it('hides the agent summary for local handler nodes', () => {
    render(
      <WorkflowNodeExecutionSection
        node={node}
        executorCatalog={executorCatalog}
        {...editorProps}
      />
    )

    expect(screen.queryByTestId('agent-node-summary')).not.toBeInTheDocument()
  })
})
