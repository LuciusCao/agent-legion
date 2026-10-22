import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RuntimeConfigCard } from './RuntimeConfigCard'
import { WorkflowDraftCard } from './WorkflowDraftCard'
import { DraftMessageCards } from './DraftMessageCards'
import { runtimeCardKey } from './draftChannelSplit'
import { useRuntimeCardSettlementStore } from './runtimeCardSettlement'
import { useAgentPublishNoticeStore } from '../shared/agentPublishNoticeStore'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { fetchWorkflowRuntimeVersions } from '../../../api'
import type { WorkflowDraftView } from './studioChatMessages'

vi.mock('../../../api/workflowDraftCompare', () => ({
  compareWorkflowDraft: vi.fn(),
}))
vi.mock('../../../api', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchWorkflowRuntimeVersions: vi.fn(),
}))
const mockVersions = vi.mocked(fetchWorkflowRuntimeVersions)

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
const PROPOSAL_MIXED = `key: ws1
nodes:
  write_script:
    type: agent
    label: 撰写脚本（精修版）
    capability: write_script
    execution:
      runtime: velites
      model: gpt-5.2
`
const PROPOSAL_RUNTIME_ONLY = ACTIVE_YAML.replace(
  'model: gpt-5.1',
  'model: gpt-5.2'
)
const PROPOSAL_STRUCT_ONLY = ACTIVE_YAML.replace(
  'label: 撰写脚本',
  'label: 撰写脚本（精修版）'
)

function makeDraft(yaml: string): WorkflowDraftView {
  return { yaml, validated: true, compareMeta: null, draftHash: null }
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
    viewMode: 'draft',
    ...overrides,
  }
}

/** 按 DraftMessageCards 的固定顺序渲染两张卡（运行配置卡在前）。 */
function renderBothCards(
  draft: WorkflowDraftView,
  studio: Record<string, unknown> | null,
  onApply = vi.fn()
) {
  const cards = (
    <>
      <RuntimeConfigCard draft={draft} workspaceId="ws1" />
      <WorkflowDraftCard draft={draft} workspaceId="ws1" onApply={onApply} />
    </>
  )
  return render(
    <TestQueryProvider>
      {studio ? withStudioProviders(studio, makeStudioView(), cards) : cards}
    </TestQueryProvider>
  )
}

describe('M65 拆卡：运行配置变更卡 / Workflow 草稿卡', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useRuntimeCardSettlementStore.setState({ settled: {} })
    useAgentPublishNoticeStore.setState({ pendingRequestId: null })
    mockVersions.mockResolvedValue({
      versions: [
        {
          version: 3,
          created_at: '2026-10-22T04:05:05+00:00',
          changed_keys: ['nodes.write_script.model'],
          created_by: 'user:test',
        },
      ],
    } as never)
  })

  it('混合建议：两卡齐备且运行配置卡固定在前', () => {
    renderBothCards(makeDraft(PROPOSAL_MIXED), makeStudio())

    const runtimeTitle = screen.getByText('运行配置变更')
    const draftTitle = screen.getByText('Workflow 草稿')
    expect(screen.getByText('运行通道 · 立即生效')).toBeInTheDocument()
    expect(screen.getByText('结构通道 · 发布生效')).toBeInTheDocument()
    expect(
      runtimeTitle.compareDocumentPosition(draftTitle) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    // runtime 卡只列 runtime 键；结构卡只列结构条目
    expect(screen.getByText('write_script.model')).toBeInTheDocument()
    expect(screen.getByText('gpt-5.2')).toBeInTheDocument()
    expect(screen.getByText(/仅结构变更（1 项）/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '应用并生效' })
    ).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /版本历史/ })).toHaveLength(1)
    expect(
      screen.getByRole('button', { name: '应用到编辑器' })
    ).toBeInTheDocument()
    // 「存为草稿」已并入「应用到编辑器」
    expect(
      screen.queryByRole('button', { name: '存为草稿' })
    ).not.toBeInTheDocument()
  })

  it('结构卡「应用到编辑器」只应用结构切片、保留当前草稿 runtime', () => {
    const onApply = vi.fn()
    renderBothCards(makeDraft(PROPOSAL_MIXED), makeStudio(), onApply)

    fireEvent.click(screen.getByRole('button', { name: '应用到编辑器' }))

    const applied = onApply.mock.calls[0][0] as string
    expect(applied).toContain('精修版')
    expect(applied).toContain('model: gpt-5.1')
    expect(applied).not.toContain('gpt-5.2')
  })

  it('纯 runtime 建议：只渲染运行配置卡', () => {
    renderBothCards(makeDraft(PROPOSAL_RUNTIME_ONLY), makeStudio())

    expect(screen.getByText('运行配置变更')).toBeInTheDocument()
    expect(screen.queryByText('Workflow 草稿')).not.toBeInTheDocument()
  })

  it('纯结构建议：只渲染 Workflow 草稿卡', () => {
    renderBothCards(makeDraft(PROPOSAL_STRUCT_ONLY), makeStudio())

    expect(screen.getByText('Workflow 草稿')).toBeInTheDocument()
    expect(screen.queryByText('运行配置变更')).not.toBeInTheDocument()
  })

  it('与现状无差异的建议：回落经典单卡（无通道徽标）', () => {
    renderBothCards(makeDraft(ACTIVE_YAML), makeStudio())

    expect(screen.getByText('Workflow 草稿')).toBeInTheDocument()
    expect(screen.queryByText('运行配置变更')).not.toBeInTheDocument()
    expect(screen.queryByText('运行通道 · 立即生效')).not.toBeInTheDocument()
    expect(screen.queryByText('结构通道 · 发布生效')).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '应用到编辑器' })
    ).toBeInTheDocument()
  })

  it('「应用并生效」成功后转入「已生效 · vN」落定态（卡片不消失）', async () => {
    const studio = makeStudio()
    renderBothCards(makeDraft(PROPOSAL_RUNTIME_ONLY), studio)

    fireEvent.click(screen.getByRole('button', { name: '应用并生效' }))
    expect(studio.saveRuntimeConfig).toHaveBeenCalledWith(PROPOSAL_RUNTIME_ONLY)

    await waitFor(() =>
      expect(screen.getByText('已生效 · v3')).toBeInTheDocument()
    )
    // 落定态：建议原文保留备查、操作区只剩版本历史；结构卡不回落经典卡。
    expect(screen.getByText('write_script.model')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '应用并生效' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /版本历史/ })).toBeInTheDocument()
    expect(screen.queryByText('Workflow 草稿')).not.toBeInTheDocument()
  })

  it('落定态在 diff 被基线推进清空后仍然保留（连续编辑语义）', () => {
    // 卡已应用（有落定记录），此刻基线已并入建议 → runtime diff 为空。
    const draft = makeDraft(PROPOSAL_RUNTIME_ONLY)
    useRuntimeCardSettlementStore
      .getState()
      .settleRuntimeCard(runtimeCardKey(draft), {
        version: 2,
        savedKeys: ['nodes.write_script.model'],
        savedAt: 0,
      })
    // originalYaml 已含 gpt-5.2：diff 为空，靠落定记录保持卡片渲染。
    const studio = makeStudio({
      originalYaml: PROPOSAL_RUNTIME_ONLY,
      draftYaml: PROPOSAL_RUNTIME_ONLY,
    })
    renderBothCards(draft, studio)

    expect(screen.getByText('已生效 · v2')).toBeInTheDocument()
    expect(screen.getByText('write_script.model')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '应用并生效' })
    ).not.toBeInTheDocument()
  })

  it('无落定记录且 diff 为空时不渲染运行配置卡（M48 回落语义）', () => {
    renderBothCards(
      makeDraft(PROPOSAL_RUNTIME_ONLY),
      makeStudio({
        originalYaml: PROPOSAL_RUNTIME_ONLY,
        draftYaml: PROPOSAL_RUNTIME_ONLY,
      })
    )

    expect(screen.queryByText('运行配置变更')).not.toBeInTheDocument()
  })

  it('发布请求挂起时结构卡展示引导条，runtime 卡不展示', () => {
    useAgentPublishNoticeStore.setState({ pendingRequestId: 'req-1' })
    renderBothCards(makeDraft(PROPOSAL_MIXED), makeStudio())

    expect(screen.getByText(/Agent 已请求发布本次结构变更/)).toBeInTheDocument()
    expect(screen.getByText(/运行配置可随时独立应用/)).toBeInTheDocument()
  })

  it('无 studio 上下文：运行配置卡不渲染，草稿卡回落经典形态', () => {
    renderBothCards(makeDraft(PROPOSAL_MIXED), null)

    expect(screen.queryByText('运行配置变更')).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '应用到编辑器' })
    ).toBeInTheDocument()
  })
})

describe('DraftMessageCards 拆卡渲染顺序（M65）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useRuntimeCardSettlementStore.setState({ settled: {} })
    useAgentPublishNoticeStore.setState({ pendingRequestId: null })
  })

  it('同一草稿消息上运行配置卡在前、Workflow 草稿卡在后', () => {
    render(
      <TestQueryProvider>
        {withStudioProviders(
          makeStudio(),
          makeStudioView(),
          <DraftMessageCards
            messageId="m1"
            workflowDraft={{
              ...makeDraft(PROPOSAL_MIXED),
              anchorToolCallId: null,
              anchorMessageId: 'm1',
            }}
            nodeDrafts={[]}
            workspaceId="ws1"
            onApplyWorkflowDraft={vi.fn()}
          />
        )}
      </TestQueryProvider>
    )

    const runtimeTitle = screen.getByText('运行配置变更')
    const draftTitle = screen.getByText('Workflow 草稿')
    expect(
      runtimeTitle.compareDocumentPosition(draftTitle) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })
})
