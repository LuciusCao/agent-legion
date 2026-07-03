import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { WorkflowEdgeConditionEditor } from './WorkflowEdgeConditionEditor'
import { parseWorkflowYaml } from '../workflowStudioYamlDraft.parse'
import type { components } from '../../../generated/api'

type WorkflowEdgeResponse = components['schemas']['WorkflowEdgeResponse']

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

  it('derives condition value from definitionYaml draft', () => {
    const { rerender } = render(
      <WorkflowEdgeConditionEditor
        edges={[edge]}
        definitionYaml={yaml}
        onDefinitionYamlChange={vi.fn()}
      />
    )
    expect(screen.getByLabelText('条件 path')).toHaveValue('$.eligible')

    const updatedYaml = yaml.replace('path: $.eligible', 'path: $.ready')
    rerender(
      <WorkflowEdgeConditionEditor
        edges={[edge]}
        definitionYaml={updatedYaml}
        onDefinitionYamlChange={vi.fn()}
      />
    )
    expect(screen.getByLabelText('条件 path')).toHaveValue('$.ready')
  })

  it('edits the second edge when source and target repeat', () => {
    const duplicateEdges = [edge, edge]
    const yamlWithDuplicateEdges = `${yaml.trim()}\n  - source: branch\n    target: left\n    condition:\n      artifact: result.json\n      path: $.eligible\n      equals: true\n`
    const onChange = vi.fn()
    render(
      <WorkflowEdgeConditionEditor
        edges={duplicateEdges}
        definitionYaml={yamlWithDuplicateEdges}
        onDefinitionYamlChange={onChange}
      />
    )

    const equalsInputs = screen.getAllByLabelText('条件 equals')
    expect(equalsInputs).toHaveLength(2)

    fireEvent.change(equalsInputs[1], {
      target: { value: 'false' },
    })

    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining('equals: false')
    )
  })

  it('edits the outgoing edge by its global YAML index, not local subset index', () => {
    const multiYaml = `key: demo
label: Demo
nodes:
  fetch:
    label: Fetch
    capability: fetch
    after: []
    inputs: []
    outputs: []
  branch:
    label: Branch
    capability: branch
    after: []
    inputs: []
    outputs: []
  left:
    label: Left
    capability: left
    after: []
    inputs: []
    outputs: []
edges:
  - source: fetch
    target: branch
  - source: branch
    target: left
    condition:
      artifact: result.json
      path: $.eligible
      equals: true
`
    const outgoingEdges: WorkflowEdgeResponse[] = [
      { source: 'branch', target: 'left', condition: edge.condition },
    ]
    const onChange = vi.fn()
    render(
      <WorkflowEdgeConditionEditor
        edges={outgoingEdges}
        definitionYaml={multiYaml}
        onDefinitionYamlChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('条件 path'), {
      target: { value: '$.ready' },
    })

    const calls = onChange.mock.calls
    const patchedYaml = calls[calls.length - 1]?.[0] as string
    const draft = parseWorkflowYaml(patchedYaml)
    expect(draft.edges?.[0].condition).toBeUndefined()
    expect(draft.edges?.[1].condition?.path).toBe('$.ready')
  })

  it('maps duplicate outgoing edges to correct global indices when preceded by other edges', () => {
    const duplicateYaml = `key: demo
label: Demo
nodes:
  fetch:
    label: Fetch
    capability: fetch
    after: []
    inputs: []
    outputs: []
  branch:
    label: Branch
    capability: branch
    after: []
    inputs: []
    outputs: []
  left:
    label: Left
    capability: left
    after: []
    inputs: []
    outputs: []
edges:
  - source: fetch
    target: branch
  - source: branch
    target: left
    condition:
      artifact: a.json
      path: $.x
      equals: true
  - source: branch
    target: left
    condition:
      artifact: b.json
      path: $.y
      equals: false
`
    const outgoingEdges: WorkflowEdgeResponse[] = [
      {
        source: 'branch',
        target: 'left',
        condition: {
          artifact: 'a.json',
          path: '$.x',
          equals: true,
        },
      },
      {
        source: 'branch',
        target: 'left',
        condition: {
          artifact: 'b.json',
          path: '$.y',
          equals: false,
        },
      },
    ]
    const onChange = vi.fn()
    render(
      <WorkflowEdgeConditionEditor
        edges={outgoingEdges}
        definitionYaml={duplicateYaml}
        onDefinitionYamlChange={onChange}
      />
    )

    const pathInputs = screen.getAllByLabelText('条件 path')
    fireEvent.change(pathInputs[1], {
      target: { value: '$.changed' },
    })

    const calls = onChange.mock.calls
    const patchedYaml = calls[calls.length - 1]?.[0] as string
    const draft = parseWorkflowYaml(patchedYaml)
    expect(draft.edges?.[0].condition).toBeUndefined()
    expect(draft.edges?.[1].condition?.path).toBe('$.x')
    expect(draft.edges?.[2].condition?.path).toBe('$.changed')
  })
})
