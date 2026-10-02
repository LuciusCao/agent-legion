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
  it.each([
    'nodes: [',
    'nodes: []',
    'nodes: invalid',
    'nodes: {_start: {type: start}, broken: null}',
    'nodes: {_start: {type: start}}\nedges: invalid',
    'nodes: {_start: {type: start}}\nedges: [null]',
    'nodes: {_start: {type: code}}',
    'nodes: {custom_start: {type: start}}',
  ])('does not offer published fields for an unsafe draft %s', (raw) => {
    const save = vi.fn()
    render(
      <WorkflowNodeStartTextInputEditor
        node={startNode(['text'])}
        definitionYaml={raw}
        setDefinitionYaml={save}
      />
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.queryByLabelText('预填模板')).not.toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })
  it('does not recreate a removed custom start node from published fallback', () => {
    const node = { ...startNode(['text']), key: 'removed-start' }
    render(
      <WorkflowNodeStartTextInputEditor
        node={node}
        definitionYaml={draftYaml}
        setDefinitionYaml={vi.fn()}
      />
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })
  it('keeps the loader synthetic _start editable when it is absent from YAML', () => {
    const save = vi.fn()
    render(
      <WorkflowNodeStartTextInputEditor
        node={startNode(['text'])}
        definitionYaml="nodes: {gen: {type: code}}"
        setDefinitionYaml={save}
      />
    )
    fireEvent.change(screen.getByLabelText('输入框标题'), {
      target: { value: '需求' },
    })
    expect(loadStart(save.mock.calls[0][0]).text_input).toEqual({
      label: '需求',
    })
  })
  it('keeps invalid YAML intact when falling back to a published record, then recovers after correction', () => {
    const setDefinitionYaml = vi.fn()
    const node = startNode(['text'], { template: '# Published\n' })
    const invalid = draftYaml + '    text_input: {template: 123}\n'
    const { rerender } = render(
      <WorkflowNodeStartTextInputEditor
        node={node}
        definitionYaml={invalid}
        setDefinitionYaml={setDefinitionYaml}
      />
    )
    expect(screen.getByRole('alert')).toHaveTextContent('text_input 格式无效')
    expect(screen.queryByLabelText('输入框标题')).not.toBeInTheDocument()
    expect(setDefinitionYaml).not.toHaveBeenCalled()
    rerender(
      <WorkflowNodeStartTextInputEditor
        node={node}
        definitionYaml={draftYaml}
        setDefinitionYaml={setDefinitionYaml}
      />
    )
    expect(screen.getByLabelText('预填模板')).toHaveValue('')
  })
  it('edits raw draft fields without copying stale published fields back into YAML', () => {
    const setDefinitionYaml = vi.fn()
    render(
      <WorkflowNodeStartTextInputEditor
        node={startNode(['text'], {
          label: 'Published',
          template: '# Published\n',
        })}
        definitionYaml={
          draftYaml +
          '    text_input: ' +
          JSON.stringify({ label: '创作需求', template: '# 需求\n' }) +
          '\n'
        }
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
