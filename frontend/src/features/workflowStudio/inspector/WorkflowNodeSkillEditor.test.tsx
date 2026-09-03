import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchWorkspaceNodeRuns } from '../../../api/workspaceNodeRunsApi'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import type { NodeRun, WorkflowNodeRecord } from '../../../types'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'
import { WorkflowNodeSkillEditor } from './WorkflowNodeSkillEditor'

const skillSelectorProps = vi.fn()

vi.mock('../../../api/workspaceNodeRunsApi', () => ({
  fetchWorkspaceNodeRuns: vi.fn(),
}))

vi.mock('../../../components/SkillSelector', () => ({
  SkillSelector: (props: {
    value: string
    onChange: (key: string) => void
    skillRef: string
    onSkillRefChange: (ref: string) => void
  }) => {
    skillSelectorProps(props)
    return (
      <div>
        <button
          type="button"
          data-testid="skill-selector-stub"
          onClick={() => props.onChange('demo/review')}
        >
          {props.value || '未选择'}
        </button>
        <button
          type="button"
          data-testid="skill-ref-stub"
          onClick={() => props.onSkillRefChange('v1.0.0')}
        >
          ref:{props.skillRef || 'latest'}
        </button>
      </div>
    )
  },
}))

const mockFetchNodeRuns = vi.mocked(fetchWorkspaceNodeRuns)

const node: WorkflowNodeRecord = {
  key: 'n1',
  label: '节点一',
  capability: 'cap',
  after: [],
  inputs: [],
  outputs: [],
}

const baseYaml = 'nodes:\n  n1:\n    capability: cap\n'

function runWithSkillVersion(skillVersion: string): NodeRun {
  return {
    id: 7,
    job_id: 'j1',
    node_key: 'n1',
    status: 'completed',
    started_at: '2026-09-01T08:00:00Z',
    finished_at: '2026-09-01T08:00:12Z',
    command_json: '[]',
    exit_code: 0,
    log_path: '/logs/run.log',
    error_message: '',
    run_dir: '',
    session_dir: '',
    runner: '',
    skill_version: skillVersion,
  }
}

function renderEditor(options?: {
  definitionYaml?: string
  setDefinitionYaml?: (value: string) => void
  node?: WorkflowNodeRecord
  readOnly?: boolean
}) {
  return render(
    <TestQueryProvider>
      <WorkflowNodeSkillEditor
        node={options?.node ?? node}
        definitionYaml={options?.definitionYaml ?? baseYaml}
        setDefinitionYaml={options?.setDefinitionYaml ?? (() => {})}
        readOnly={options?.readOnly}
      />
    </TestQueryProvider>
  )
}

describe('WorkflowNodeSkillEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1' })
    mockFetchNodeRuns.mockResolvedValue([])
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

  it('resets the ref to latest when switching to a different skill (codex r2 P1 on #427)', () => {
    // 节点已绑定 demo/review@v1.0.0 后校验选择新 skill（stub 固定回填
    // demo/other）：换 key 不携带旧 skill 的 tag——B@v1.0.0 无法被 B 的仓库
    // 解析，发布后首次 dispatch 才失败。
    const setDefinitionYaml = vi.fn()
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill:\n      key: demo/review\n      ref: v1.0.0\n',
      setDefinitionYaml,
    })

    // 模拟换绑：SkillSelector 校验回填另一个 key（经受控 value 传回）。
    const onChange = skillSelectorProps.mock.calls[
      skillSelectorProps.mock.calls.length - 1
    ][0].onChange as (key: string) => void
    onChange('demo/other')

    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.skill).toEqual({
      key: 'demo/other',
      ref: 'latest',
    })
  })

  it('keeps the picked ref when re-validating the same skill (codex r2 P1 on #427)', () => {
    // 同一 key 重新校验：已选版本保留，不因换绑逻辑被重置。
    const setDefinitionYaml = vi.fn()
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill:\n      key: demo/review\n      ref: v1.0.0\n',
      setDefinitionYaml,
    })

    const onChange = skillSelectorProps.mock.calls[
      skillSelectorProps.mock.calls.length - 1
    ][0].onChange as (key: string) => void
    onChange('demo/review')

    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.skill).toEqual({
      key: 'demo/review',
      ref: 'v1.0.0',
    })
  })

  it('patches the version select choice as the ref onto the bound key (#410)', () => {
    const setDefinitionYaml = vi.fn()
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill: demo/review\n',
      setDefinitionYaml,
    })

    fireEvent.click(screen.getByTestId('skill-ref-stub'))

    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.skill).toEqual({
      key: 'demo/review',
      ref: 'v1.0.0',
    })
  })

  it('echoes the bound skill and ref from the draft yaml', () => {
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill:\n      key: demo/review\n      ref: v1.0.0\n',
    })

    expect(skillSelectorProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: 'demo/review', skillRef: 'v1.0.0' })
    )
  })

  it('normalizes a string-form draft binding to ref latest (#322)', () => {
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill: demo/review\n',
    })

    expect(skillSelectorProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ skillRef: 'latest' })
    )
  })

  it('echoes the published node skill when the draft has no such node', () => {
    renderEditor({
      node: { ...node, skill: { key: 'demo/other', ref: 'v2.0.0' } },
      definitionYaml: 'nodes:\n  other_node:\n    capability: cap\n',
    })

    expect(skillSelectorProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: 'demo/other', skillRef: 'v2.0.0' })
    )
  })

  it('stays unbound after the binding is cleared from the draft (no published echo)', () => {
    // codex P2 on PR 317：草稿节点存在但无 skill key = 显式清除；回显
    // published 绑定会让「清除 skill 绑定」立刻被旧值覆盖。
    renderEditor({
      node: { ...node, skill: { key: 'demo/other', ref: 'v2.0.0' } },
      definitionYaml: baseYaml, // 草稿节点 n1 存在、无 skill key
    })

    expect(skillSelectorProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: '', skillRef: '' })
    )
    expect(
      screen.queryByRole('button', { name: '清除 skill 绑定' })
    ).not.toBeInTheDocument()
  })

  it('echoes the latest-resolved skill version from the most recent run (#410)', async () => {
    mockFetchNodeRuns.mockResolvedValue([
      runWithSkillVersion('latest@abc123def456'),
    ])
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill: demo/review\n',
    })

    expect(
      await screen.findByText('实际执行：latest@abc123def456')
    ).toBeInTheDocument()
    expect(mockFetchNodeRuns).toHaveBeenCalledWith('ws1', {
      nodeKey: 'n1',
      limit: 1,
    })
  })

  it('does not query run history for a pinned tag binding (#410)', () => {
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill:\n      key: demo/review\n      ref: v1.0.0\n',
    })

    expect(mockFetchNodeRuns).not.toHaveBeenCalled()
  })

  it('shows no latest echo when the node has no runs (#410)', async () => {
    renderEditor({
      definitionYaml:
        'nodes:\n  n1:\n    capability: cap\n    skill: demo/review\n',
    })

    await waitFor(() => expect(mockFetchNodeRuns).toHaveBeenCalled())
    expect(screen.queryByText(/实际执行/)).not.toBeInTheDocument()
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
