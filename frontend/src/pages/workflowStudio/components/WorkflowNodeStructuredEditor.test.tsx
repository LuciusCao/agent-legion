import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { WorkflowNodeStructuredEditor } from './WorkflowNodeStructuredEditor'
import type { WorkflowNodeRecord } from '../../../types'

const node: WorkflowNodeRecord = {
  key: 'fetch',
  label: 'Fetch',
  capability: 'fetch_questions',
  after: [],
  inputs: [],
  outputs: ['questions.json'],
  terminal: null,
}

const yaml = `key: demo
label: Demo
nodes:
  fetch:
    label: Fetch
    capability: fetch_questions
    after: []
    inputs: []
    outputs:
      - questions.json
`

describe('WorkflowNodeStructuredEditor', () => {
  it('updates yaml when node label changes', () => {
    const onChange = vi.fn()
    render(
      <WorkflowNodeStructuredEditor
        node={node}
        definitionYaml={yaml}
        onDefinitionYamlChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('节点名称'), {
      target: { value: 'Fetch v2' },
    })

    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining('label: Fetch v2')
    )
  })

  it('updates yaml when capability changes', () => {
    const onChange = vi.fn()
    render(
      <WorkflowNodeStructuredEditor
        node={node}
        definitionYaml={yaml}
        onDefinitionYamlChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('能力'), {
      target: { value: 'fetch_questions_v2' },
    })

    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining('capability: fetch_questions_v2')
    )
  })

  it('derives input values from definitionYaml draft', () => {
    const { rerender } = render(
      <WorkflowNodeStructuredEditor
        node={node}
        definitionYaml={yaml}
        onDefinitionYamlChange={vi.fn()}
      />
    )
    expect(screen.getByLabelText('节点名称')).toHaveValue('Fetch')

    const updatedYaml = yaml.replace('label: Fetch', 'label: Fetch v2')
    rerender(
      <WorkflowNodeStructuredEditor
        node={node}
        definitionYaml={updatedYaml}
        onDefinitionYamlChange={vi.fn()}
      />
    )
    expect(screen.getByLabelText('节点名称')).toHaveValue('Fetch v2')
  })
})
