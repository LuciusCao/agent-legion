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

  it('writes the validated skill key into the draft yaml with ref defaulting to latest', () => {
    const setDefinitionYaml = vi.fn()
    renderEditor({ setDefinitionYaml })

    fireEvent.click(screen.getByTestId('skill-selector-stub'))

    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.skill).toEqual({
      key: 'demo/review',
      ref: 'latest',
    })
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

  it('echoes the bound skill from the draft yaml and the latest helper text', () => {
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill:\n      key: demo/review\n      ref: v1.0.0\n',
    })

    expect(skillSelectorProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: 'demo/review' })
    )
    expect(screen.getByLabelText('Skill ref')).toHaveValue('v1.0.0')
    expect(
      screen.getByText('latest = 跟随仓库最新提交；填 tag 锁定版本')
    ).toBeInTheDocument()
  })

  it('normalizes a string-form draft binding to ref latest (#322)', () => {
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill: demo/review\n',
    })

    expect(screen.getByLabelText('Skill ref')).toHaveValue('latest')
  })

  it('echoes the published node skill when the draft has no such node', () => {
    renderEditor({
      node: { ...node, skill: { key: 'demo/other', ref: 'v2.0.0' } },
      definitionYaml: 'nodes:\n  other_node:\n    capability: cap\n',
    })

    expect(skillSelectorProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: 'demo/other' })
    )
    expect(screen.getByLabelText('Skill ref')).toHaveValue('v2.0.0')
  })

  it('stays unbound after the binding is cleared from the draft (no published echo)', () => {
    // codex P2 on PR 317：草稿节点存在但无 skill key = 显式清除；回显
    // published 绑定会让「清除 skill 绑定」立刻被旧值覆盖。
    renderEditor({
      node: { ...node, skill: { key: 'demo/other', ref: 'v2.0.0' } },
      definitionYaml: baseYaml, // 草稿节点 n1 存在、无 skill key
    })

    expect(skillSelectorProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: '' })
    )
    expect(screen.getByLabelText('Skill ref')).toHaveValue('')
    expect(screen.getByLabelText('Skill ref')).toBeDisabled()
    expect(
      screen.queryByRole('button', { name: '清除 skill 绑定' })
    ).not.toBeInTheDocument()
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
