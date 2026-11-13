import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { WorkflowNodeStructuredEditor } from './WorkflowNodeStructuredEditor'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'
import type { WorkflowNodeRecord } from '../../../types'

const node: WorkflowNodeRecord = {
  key: 'fetch',
  label: 'Fetch',
  capability: 'fetch_items',
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
    capability: fetch_items
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
      target: { value: 'fetch_items_v2' },
    })

    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining('capability: fetch_items_v2')
    )
  })

  it('does not revive the published capability when the draft omits it', () => {
    const draftWithoutCapability = yaml.replace(
      '    capability: fetch_items\n',
      ''
    )
    render(
      <WorkflowNodeStructuredEditor
        node={node}
        definitionYaml={draftWithoutCapability}
        onDefinitionYamlChange={vi.fn()}
      />
    )

    expect(screen.getByLabelText('能力')).toHaveValue('')
    expect(screen.getByLabelText('节点名称')).toHaveValue('Fetch')
  })

  it('clears capability without losing values, renaming it, or published fallback', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <WorkflowNodeStructuredEditor
        node={node}
        definitionYaml={yaml}
        onDefinitionYamlChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('能力'), { target: { value: '' } })

    const nextYaml = onChange.mock.calls[0][0] as string
    expect(nextYaml).toContain("capability: ''")
    expect(nextYaml).toContain('label: Fetch')
    expect(nextYaml).toContain('questions.json')
    expect(nextYaml).not.toContain('capability: fetch')
    rerender(
      <WorkflowNodeStructuredEditor
        node={node}
        definitionYaml={nextYaml}
        onDefinitionYamlChange={onChange}
      />
    )
    expect(screen.getByLabelText('能力')).toHaveValue('')
    expect(screen.getByLabelText('节点名称')).toHaveValue('Fetch')
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

  it('M48：compare 报告结构字段变更时挂「已修改 · 待发布」标记', () => {
    const studio = {
      compareSummary: {
        createsRevision: true,
        riskLevel: 'info',
        severityLabel: '提示',
        nodeChanges: [
          {
            type: 'modified',
            nodeKey: 'fetch',
            label: 'Fetch',
            nodeType: 'code',
            fields: ['label'],
            severity: 'info',
          },
        ],
        edgeChanges: [],
        intakeChanges: [],
        metadataChanges: [],
        riskFlags: [],
        changedNodeKeys: new Set(['fetch']),
      },
    }
    render(
      withStudioProviders(
        studio,
        makeStudioView(),
        <WorkflowNodeStructuredEditor
          node={node}
          definitionYaml={yaml}
          onDefinitionYamlChange={vi.fn()}
        />
      )
    )

    expect(screen.getByText('已修改 · 待发布')).toBeInTheDocument()
  })
})
