import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowDraftCard } from './WorkflowDraftCard'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'

vi.mock('../../../api/workflowDraftCompare', () => ({
  compareWorkflowDraft: vi.fn(),
}))

const ACTIVE_YAML = `key: ws1
nodes:
  write_script:
    type: agent
    label: 撰写脚本
    capability: write_script
    execution:
      runtime: velites
      model: gpt-5.1
`

// agent 建议：改 model（运行通道）+ 改 label（结构通道）。
const PROPOSAL_YAML = `key: ws1
nodes:
  write_script:
    type: agent
    label: 撰写脚本（精修版）
    capability: write_script
    execution:
      runtime: velites
      model: gpt-5.2
`

const draft = {
  yaml: PROPOSAL_YAML,
  validated: true,
  compareMeta: null,
  draftHash: null,
}

function makeStudio(overrides: Record<string, unknown> = {}) {
  return {
    canPublish: true,
    createsRevision: true,
    publishing: false,
    validating: false,
    compareState: 'ready',
    compareErrors: null,
    compareSummary: null,
    originalYaml: ACTIVE_YAML,
    draftYaml: ACTIVE_YAML,
    runtimeSaving: false,
    saveRuntimeConfig: vi.fn(() => Promise.resolve(true)),
    nodes: [{ key: 'write_script' }],
    focusNonce: 0,
    requestNodeFocus: vi.fn(),
    requestPublish: vi.fn(),
    setSelectedNodeKey: vi.fn(),
    ...overrides,
  }
}

function renderCard(studio: Record<string, unknown> | null, onApply = vi.fn()) {
  const card = (
    <WorkflowDraftCard draft={draft} workspaceId="ws1" onApply={onApply} />
  )
  return render(
    studio ? withStudioProviders(studio, makeStudioView(), card) : card
  )
}

describe('WorkflowDraftCard 双通道分组（M48 原型第 5 屏）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('按通道分组：运行配置组「应用并生效」+ 结构变更组「存为草稿」', () => {
    renderCard(makeStudio())

    expect(screen.getByText('运行通道')).toBeInTheDocument()
    expect(screen.getByText('运行配置')).toBeInTheDocument()
    expect(screen.getByText('结构通道')).toBeInTheDocument()
    expect(screen.getByText('结构变更')).toBeInTheDocument()
    // 两组各 1 项（model / label）
    expect(screen.getAllByText('（1 项）')).toHaveLength(2)
    expect(
      screen.getByRole('button', { name: '应用并生效' })
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '存为草稿' })).toBeInTheDocument()
  })

  it('「应用并生效」走运行通道保存（建议 YAML 作 runtime 来源）', () => {
    const studio = makeStudio()
    renderCard(studio)

    fireEvent.click(screen.getByRole('button', { name: '应用并生效' }))

    expect(studio.saveRuntimeConfig).toHaveBeenCalledWith(PROPOSAL_YAML)
  })

  it('「存为草稿」应用建议结构、保留当前草稿的 runtime 切片', () => {
    const onApply = vi.fn()
    renderCard(makeStudio(), onApply)

    fireEvent.click(screen.getByRole('button', { name: '存为草稿' }))

    const applied = onApply.mock.calls[0][0] as string
    expect(applied).toContain('精修版')
    // 当前草稿（= active）的 runtime 值保留，建议的 model 不随结构组入草稿。
    expect(applied).toContain('model: gpt-5.1')
    expect(applied).not.toContain('gpt-5.2')
  })

  it('无运行配置差异时不渲染运行通道组', () => {
    const runtimeOnlyDraft = {
      ...draft,
      yaml: ACTIVE_YAML.replace('label: 撰写脚本', 'label: 撰写脚本（精修版）'),
    }
    render(
      withStudioProviders(
        makeStudio(),
        makeStudioView(),
        <WorkflowDraftCard
          draft={runtimeOnlyDraft}
          workspaceId="ws1"
          onApply={vi.fn()}
        />
      )
    )

    expect(screen.queryByText('运行通道')).not.toBeInTheDocument()
    expect(screen.getByText('结构通道')).toBeInTheDocument()
  })

  it('无 studio 上下文时退化为单键应用（测试直渲染兼容）', () => {
    renderCard(null)

    expect(screen.queryByText('运行通道')).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '应用到编辑器' })
    ).toBeInTheDocument()
  })
})
