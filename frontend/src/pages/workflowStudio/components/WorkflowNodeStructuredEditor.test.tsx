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

  it('updates yaml when inputs change', () => {
    const onChange = vi.fn()
    render(
      <WorkflowNodeStructuredEditor
        node={node}
        definitionYaml={yaml}
        onDefinitionYamlChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('输入产物，每行一个'), {
      target: { value: 'a.json\nb.json' },
    })

    expect(onChange).toHaveBeenLastCalledWith(expect.stringContaining('a.json'))
  })

  it('updates yaml when terminal outcome changes', () => {
    const terminalNode: WorkflowNodeRecord = {
      ...node,
      terminal: { outcome: 'uploadable' },
    }
    const onChange = vi.fn()
    render(
      <WorkflowNodeStructuredEditor
        node={terminalNode}
        definitionYaml={yaml}
        onDefinitionYamlChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('Terminal Outcome'), {
      target: { value: 'archived' },
    })

    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining('outcome: archived')
    )
  })
})
