import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import yaml from 'js-yaml'
import { WorkflowNodeStartTextInputEditor } from './WorkflowNodeStartTextInputEditor'
import { WorkflowNodeStartSection } from './WorkflowNodeStartSection'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'
import type { WorkflowNodeRecord } from '../../../types'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'

const draftYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '    accepted_item_types: [material, text]',
  '',
].join('\n')

function startNode(
  types: string[],
  textInput?: { label?: string; filename?: string; template?: string }
): WorkflowNodeRecord {
  return {
    key: '_start',
    label: '入口',
    capability: '',
    node_type: 'start',
    accepted_item_types: types,
    text_input: textInput ?? null,
    after: [],
    inputs: [],
    outputs: [],
  } as unknown as WorkflowNodeRecord
}

const loadStart = (raw: string) =>
  (yaml.load(raw) as { nodes: { _start: Record<string, unknown> } }).nodes
    ._start

describe('WorkflowNodeStartTextInputEditor', () => {
  it('shows the current block and patches a single field back to YAML', () => {
    const setDefinitionYaml = vi.fn()
    render(
      <WorkflowNodeStartTextInputEditor
        node={startNode(['text'], { label: '创作需求', template: '# 需求\n' })}
        definitionYaml={draftYaml}
        setDefinitionYaml={setDefinitionYaml}
      />
    )
    expect(screen.getByLabelText('输入框标题')).toHaveValue('创作需求')
    expect(screen.getByLabelText('预填模板')).toHaveValue('# 需求\n')

    fireEvent.change(screen.getByLabelText('落盘文件名'), {
      target: { value: '创作需求.md' },
    })

    const next = setDefinitionYaml.mock.calls[0][0] as string
    expect(loadStart(next).text_input).toEqual({
      label: '创作需求',
      filename: '创作需求.md',
      template: '# 需求\n',
    })
  })
})

describe('WorkflowNodeStartSection × text_input editor', () => {
  const details = (node: WorkflowNodeRecord): SelectedWorkflowNodeDetails => ({
    node,
    incoming: [],
    outgoing: [],
  })
  const renderSection = (node: WorkflowNodeRecord, readOnly = false) =>
    render(
      <MemoryRouter>
        <WorkflowNodeStartSection
          details={details(node)}
          definitionYaml={draftYaml}
          setDefinitionYaml={vi.fn()}
          readOnly={readOnly}
        />
      </MemoryRouter>
    )

  it('renders the editor only when the contract accepts text', () => {
    renderSection(startNode(['material', 'text']))
    expect(screen.getByTestId('start-text-input-editor')).toBeInTheDocument()
  })

  it('hides the editor without text in the contract or in readOnly mode', () => {
    const { unmount } = renderSection(startNode(['material']))
    expect(
      screen.queryByTestId('start-text-input-editor')
    ).not.toBeInTheDocument()
    unmount()
    renderSection(startNode(['text']), true)
    expect(
      screen.queryByTestId('start-text-input-editor')
    ).not.toBeInTheDocument()
  })
})
