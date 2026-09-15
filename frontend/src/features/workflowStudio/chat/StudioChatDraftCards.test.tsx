import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowDraftCard } from './StudioChatDraftCards'
import { compareWorkflowDraft } from '../../../api/workflowDraftCompare'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'

vi.mock('../../../api/workflowDraftCompare', () => ({
  compareWorkflowDraft: vi.fn(),
}))
const mockCompare = vi.mocked(compareWorkflowDraft)

const draft = {
  yaml: 'key: demo_video_workflow\nnodes: []\n',
  validated: true,
  compareMeta: null,
}

function makeStudio(overrides: Record<string, unknown> = {}) {
  return {
    canPublish: true,
    createsRevision: true,
    actionState: 'idle',
    compareState: 'ready',
    compareErrors: null,
    compareSummary: null,
    definitionYaml: draft.yaml,
    requestPublish: vi.fn(),
    setSelectedNodeKey: vi.fn(),
    ...overrides,
  }
}

function renderCard(studio: Record<string, unknown> | null) {
  const card = (
    <WorkflowDraftCard draft={draft} workspaceId="ws1" onApply={vi.fn()} />
  )
  return render(
    studio ? withStudioProviders(studio, makeStudioView(), card) : card
  )
}

function compareResponseWithNodeChange() {
  return {
    valid: true,
    creates_revision: true,
    base_revision: null,
    draft_workflow: null,
    errors: [],
    summary: {
      risk_level: 'info' as const,
      node_changes: [
        {
          type: 'modified' as const,
          node_key: 'n_extract',
          label: '提取',
          node_type: 'code' as const,
          fields: ['config'],
          risk: 'info' as const,
        },
      ],
      edge_changes: [],
      intake_changes: [],
      metadata_changes: [],
      risk_flags: [],
    },
  }
}

describe('WorkflowDraftCard 发布入口（#667 B1）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('无 Studio Provider 时不渲染发布按钮（测试直渲染降级）', () => {
    renderCard(null)
    expect(
      screen.getByRole('button', { name: '查看 diff' })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '发布新版本' })
    ).not.toBeInTheDocument()
  })

  it('canPublish 时发布按钮可用，点击调 requestPublish', () => {
    const studio = makeStudio()
    renderCard(studio)

    const publish = screen.getByRole('button', { name: '发布新版本' })
    expect(publish).toBeEnabled()
    fireEvent.click(publish)
    expect(studio.requestPublish).toHaveBeenCalledTimes(1)
  })

  it('createsRevision 为 false 时文案为「保存运行配置」（对齐命令条）', () => {
    renderCard(makeStudio({ createsRevision: false }))
    expect(
      screen.getByRole('button', { name: '保存运行配置' })
    ).toBeInTheDocument()
  })

  it('canPublish 为假时禁用并在 title 说明原因', () => {
    renderCard(makeStudio({ canPublish: false, compareSummary: null }))
    const publish = screen.getByRole('button', { name: '发布新版本' })
    expect(publish).toBeDisabled()
    expect(publish.parentElement).toHaveAttribute(
      'title',
      '与 active revision 没有可发布的变更'
    )
  })

  it('compare 进行中时禁用并提示稍候', () => {
    renderCard(makeStudio({ canPublish: false, compareState: 'loading' }))
    const publish = screen.getByRole('button', { name: '发布新版本' })
    expect(publish).toBeDisabled()
    expect(publish.parentElement).toHaveAttribute(
      'title',
      '正在与 active revision 对比，请稍候'
    )
  })

  it('canPublish 为真但 actionState 非 idle（publish POST 在途）时禁用', () => {
    // canPublish 不含 actionState：确认框关闭后首个 POST 在途，按钮必须
    // 保持禁用，防止再开确认框发起第二个 POST（命令条同口径）。
    const studio = makeStudio({ actionState: 'publishing' })
    renderCard(studio)
    const publish = screen.getByRole('button', { name: '发布新版本' })
    expect(publish).toBeDisabled()
    expect(publish.parentElement).toHaveAttribute(
      'title',
      '校验或保存进行中，请稍候'
    )
    fireEvent.click(publish)
    expect(studio.requestPublish).not.toHaveBeenCalled()
  })

  it('草稿与编辑器内容不一致时提示发布以编辑器 YAML 为准', () => {
    renderCard(makeStudio({ definitionYaml: 'key: other\n' }))
    expect(screen.getByText(/发布将以编辑器中的 YAML 为准/)).toBeInTheDocument()
  })

  it('草稿与编辑器内容一致时不显示提示', () => {
    renderCard(makeStudio())
    expect(
      screen.queryByText(/发布将以编辑器中的 YAML 为准/)
    ).not.toBeInTheDocument()
  })
})

describe('WorkflowDraftCard diff 变更节点定位（#667 B2）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCompare.mockResolvedValue(compareResponseWithNodeChange())
  })

  it('点击 diff 里的变更节点：选中该节点并关闭 dialog', async () => {
    const studio = makeStudio()
    renderCard(studio)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '查看 diff' }))
    })
    await waitFor(() => expect(mockCompare).toHaveBeenCalled())
    const nodeItem = await screen.findByText('提取: 节点配置值')
    fireEvent.click(nodeItem)

    expect(studio.setSelectedNodeKey).toHaveBeenCalledWith('n_extract')
    await waitFor(() =>
      expect(
        screen.queryByText('草稿与 active revision 的差异')
      ).not.toBeInTheDocument()
    )
  })

  it('无 Studio Provider 时变更节点不可点击（不抛错、不回退既有展示）', async () => {
    renderCard(null)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '查看 diff' }))
    })
    const nodeItem = await screen.findByText('提取: 节点配置值')
    // 无 onSelectNode：保持纯文本展示，点击无副作用（无 Provider 可写）。
    fireEvent.click(nodeItem)
    expect(
      screen.getByText('草稿与 active revision 的差异')
    ).toBeInTheDocument()
  })
})
