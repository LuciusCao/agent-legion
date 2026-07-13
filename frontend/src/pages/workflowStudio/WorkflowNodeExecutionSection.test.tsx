import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ExecutorDefinition } from '../../executorTypes'
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
      },
    ],
  },
]

describe('WorkflowNodeExecutionSection', () => {
  it('shows the executor binding for the selected node capability', () => {
    render(
      <WorkflowNodeExecutionSection
        node={node}
        executorCatalog={executorCatalog}
      />
    )

    expect(screen.getAllByText('pi')).toHaveLength(2)
    expect(
      screen.getByText('question_comprehension_info/generate_key_info')
    ).toBeInTheDocument()
    expect(screen.getByText('read, write, bash')).toBeInTheDocument()
    expect(screen.queryByText('local-default')).not.toBeInTheDocument()
  })

  it('shows an empty state when no executor supports the capability', () => {
    render(
      <WorkflowNodeExecutionSection
        node={{ ...node, capability: 'missing' }}
        executorCatalog={executorCatalog}
      />
    )

    expect(screen.getByText('未匹配到 executor capability')).toBeInTheDocument()
  })
})
