import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { WorkflowEdgeConditionEditor } from './WorkflowEdgeConditionEditor'
import type { WorkflowEdgeResponse } from '../../../types'

const edge: WorkflowEdgeResponse = {
  source: 'branch',
  target: 'left',
  condition: {
    artifact: 'result.json',
    path: '$.eligible',
    equals: true,
  },
}

const yaml = `key: demo
label: Demo
nodes:
  branch:
    label: Branch
    capability: branch
    after: []
    inputs: []
    outputs: []
edges:
  - source: branch
    target: left
    condition:
      artifact: result.json
      path: $.eligible
      equals: true
`

describe('WorkflowEdgeConditionEditor', () => {
  it('updates edge condition yaml', () => {
    const onChange = vi.fn()
    render(
      <WorkflowEdgeConditionEditor
        edges={[edge]}
        definitionYaml={yaml}
        onDefinitionYamlChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('条件 path'), {
      target: { value: '$.eligible' },
    })
    fireEvent.change(screen.getByLabelText('条件 equals'), {
      target: { value: 'false' },
    })

    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining('equals: false')
    )
  })

  it('clears edge condition', () => {
    const onChange = vi.fn()
    render(
      <WorkflowEdgeConditionEditor
        edges={[edge]}
        definitionYaml={yaml}
        onDefinitionYamlChange={onChange}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: '清除条件' }))

    expect(onChange).toHaveBeenLastCalledWith(
      expect.not.stringContaining('condition:')
    )
  })
})
