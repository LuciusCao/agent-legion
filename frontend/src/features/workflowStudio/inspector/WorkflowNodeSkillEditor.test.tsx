import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkflowNodeRecord } from '../../../types'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'
import { WorkflowNodeSkillEditor } from './WorkflowNodeSkillEditor'

const skillSelectorProps = vi.fn()

vi.mock('../../../components/SkillSelector', () => ({
  SkillSelector: (props: {
    value: string
    onChange: (key: string) => void
  }) => {
    skillSelectorProps(props)
    return (
      <button
        type="button"
        data-testid="skill-selector-stub"
        onClick={() => props.onChange('demo/review')}
      >
        {props.value || '未选择'}
      </button>
    )
  },
}))

const node: WorkflowNodeRecord = {
  key: 'n1',
  label: '节点一',
  capability: 'cap',
  after: [],
  inputs: [],
  outputs: [],
}

const baseYaml = 'nodes:\n  n1:\n    capability: cap\n'

function renderEditor(options?: {
  definitionYaml?: string
  setDefinitionYaml?: (value: string) => void
  node?: WorkflowNodeRecord
  readOnly?: boolean
}) {
  return render(
    <WorkflowNodeSkillEditor
      node={options?.node ?? node}
      definitionYaml={options?.definitionYaml ?? baseYaml}
      setDefinitionYaml={options?.setDefinitionYaml ?? (() => {})}
      readOnly={options?.readOnly}
    />
  )
}

describe('WorkflowNodeSkillEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('writes the validated skill key into the draft yaml', () => {
    const setDefinitionYaml = vi.fn()
    renderEditor({ setDefinitionYaml })

    fireEvent.click(screen.getByTestId('skill-selector-stub'))

    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.skill).toBe('demo/review')
  })

  it('patches the ref as a mapping form onto the bound key', () => {
    const setDefinitionYaml = vi.fn()
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill: demo/review\n',
      setDefinitionYaml,
    })

    fireEvent.change(screen.getByLabelText('Skill ref'), {
      target: { value: 'v1.0.0' },
    })

    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.skill).toEqual({
      key: 'demo/review',
      ref: 'v1.0.0',
    })
  })

  it('echoes the bound skill from the draft yaml and the ref placeholder', () => {
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill:\n      key: demo/review\n      ref: v1.0.0\n',
    })

    expect(skillSelectorProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: 'demo/review' })
    )
    expect(screen.getByLabelText('Skill ref')).toHaveValue('v1.0.0')
    expect(screen.getByPlaceholderText('留空用源默认 ref')).toBeInTheDocument()
  })

  it('falls back to the published node skill when the draft has none', () => {
    renderEditor({
      node: { ...node, skill: { key: 'demo/other', ref: 'v2.0.0' } },
    })

    expect(skillSelectorProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: 'demo/other' })
    )
    expect(screen.getByLabelText('Skill ref')).toHaveValue('v2.0.0')
  })

  it('disables the ref input until a skill is bound', () => {
    renderEditor()

    expect(screen.getByLabelText('Skill ref')).toBeDisabled()
  })

  it('clears the binding', () => {
    const setDefinitionYaml = vi.fn()
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill: demo/review\n',
      setDefinitionYaml,
    })

    fireEvent.click(screen.getByRole('button', { name: '清除 skill 绑定' }))

    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.skill).toBeUndefined()
  })

  it('renders a read-only summary in read-only mode', () => {
    renderEditor({
      readOnly: true,
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill:\n      key: demo/review\n      ref: v1.0.0\n',
    })

    expect(screen.getByDisplayValue('demo/review @ v1.0.0')).toBeInTheDocument()
    expect(screen.queryByTestId('skill-selector-stub')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '清除 skill 绑定' })
    ).not.toBeInTheDocument()
  })
})
