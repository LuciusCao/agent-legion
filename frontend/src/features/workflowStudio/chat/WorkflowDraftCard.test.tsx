import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowDraftCard } from './WorkflowDraftCard'
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
    definitionYaml: draft.yaml,
    nodes: [{ key: 'n_extract' }],
    focusNonce: 0,
    requestNodeFocus: vi.fn(),
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

  it('canPublish 为真但发布在途（publish POST 未回）时禁用', () => {
    // canPublish 不含在途态：确认框关闭后首个 POST 在途，按钮必须
    // 保持禁用，防止再开确认框发起第二个 POST（命令条同口径）。
    const studio = makeStudio({ publishing: true })
    renderCard(studio)
    const publish = screen.getByRole('button', { name: '发布新版本' })
    expect(publish).toBeDisabled()
    expect(publish.parentElement).toHaveAttribute('title', '发布进行中，请稍候')
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

  // #1143（方案 B）：hash 身份核对——修复「应用到编辑器后画布规范化重排
  // 导致的字节不同」误报；hash 缺失时降级回字符串全等的既有行为。
  describe('草稿卡一致性 hash 身份核对（#1143）', () => {
    // 复现流：agent 保存草稿（hash H）→ 用户「应用到编辑器」→ 画布按自身
    // 序列化规范重写 YAML（字节不同、语义相同、hash 相同）→ 不再误报。
    it('hash 相同：编辑器重排后的 YAML 字节不同也不提示', () => {
      const studio = makeStudio({
        definitionYaml: 'nodes: []\nkey: demo_video_workflow\n',
        draftSave: {
          status: 'saved',
          savedAt: '2026-10-10T01:00:00+00:00',
          savedHash: 'h1',
        },
      })
      render(
        withStudioProviders(
          studio,
          makeStudioView(),
          <WorkflowDraftCard
            draft={{ ...draft, draftHash: 'h1' }}
            workspaceId="ws1"
            onApply={vi.fn()}
          />
        )
      )
      expect(
        screen.queryByText(/发布将以编辑器中的 YAML 为准/)
      ).not.toBeInTheDocument()
    })

    it('hash 不同：真实分歧仍提示', () => {
      const studio = makeStudio({
        definitionYaml: 'key: human-edited\n',
        draftSave: {
          status: 'saved',
          savedAt: '2026-10-10T01:00:00+00:00',
          savedHash: 'h2',
        },
      })
      render(
        withStudioProviders(
          studio,
          makeStudioView(),
          <WorkflowDraftCard
            draft={{ ...draft, draftHash: 'h1' }}
            workspaceId="ws1"
            onApply={vi.fn()}
          />
        )
      )
      expect(
        screen.getByText(/发布将以编辑器中的 YAML 为准/)
      ).toBeInTheDocument()
    })

    it('卡上无 hash（旧转录）：降级字符串比较，字节不同仍提示', () => {
      const studio = makeStudio({
        definitionYaml: 'key: other\n',
        draftSave: {
          status: 'saved',
          savedAt: '2026-10-10T01:00:00+00:00',
          savedHash: 'h1',
        },
      })
      render(
        withStudioProviders(
          studio,
          makeStudioView(),
          <WorkflowDraftCard
            draft={draft}
            workspaceId="ws1"
            onApply={vi.fn()}
          />
        )
      )
      expect(
        screen.getByText(/发布将以编辑器中的 YAML 为准/)
      ).toBeInTheDocument()
    })

    it('编辑器侧无 savedHash（未保存过/旧服务端）：降级字符串比较', () => {
      const studio = makeStudio({
        definitionYaml: 'key: other\n',
        draftSave: { status: 'idle', savedAt: null },
      })
      render(
        withStudioProviders(
          studio,
          makeStudioView(),
          <WorkflowDraftCard
            draft={{ ...draft, draftHash: 'h1' }}
            workspaceId="ws1"
            onApply={vi.fn()}
          />
        )
      )
      expect(
        screen.getByText(/发布将以编辑器中的 YAML 为准/)
      ).toBeInTheDocument()
    })

    // 评审 P2-1：hash 短路仅在编辑器当前内容已落盘（状态 settled）时
    // 有效。pending（debounce 窗口）/saving（PUT 在途）/error（失败退避
    // 或冲突挂起）= 编辑器有未落盘编辑——savedHash 停留在上次成功保存的
    // 身份，字节已变（语义可能已变）而 hash 仍相同，必须回落提示，否则
    // 发布 flush-first 发出编辑后的 YAML 却无警示。
    it('hash 相同但编辑器有未保存编辑（pending/saving/error）→ 仍提示（评审 P2-1）', () => {
      for (const status of ['pending', 'saving', 'error'] as const) {
        const studio = makeStudio({
          definitionYaml: 'nodes: []\nkey: demo_video_workflow\n',
          draftSave: {
            status,
            savedAt: '2026-10-10T01:00:00+00:00',
            savedHash: 'h1',
          },
        })
        render(
          withStudioProviders(
            studio,
            makeStudioView(),
            <WorkflowDraftCard
              draft={{ ...draft, draftHash: 'h1' }}
              workspaceId="ws1"
              onApply={vi.fn()}
            />
          )
        )
        expect(
          screen.getByText(/发布将以编辑器中的 YAML 为准/)
        ).toBeInTheDocument()
        cleanup()
      }
    })

    it('hash 相同且编辑器无未保存编辑（idle，hydrate/adopt 后内容=已落盘）→ 不提示', () => {
      const studio = makeStudio({
        definitionYaml: 'nodes: []\nkey: demo_video_workflow\n',
        draftSave: {
          status: 'idle',
          savedAt: '2026-10-10T01:00:00+00:00',
          savedHash: 'h1',
        },
      })
      render(
        withStudioProviders(
          studio,
          makeStudioView(),
          <WorkflowDraftCard
            draft={{ ...draft, draftHash: 'h1' }}
            workspaceId="ws1"
            onApply={vi.fn()}
          />
        )
      )
      expect(
        screen.queryByText(/发布将以编辑器中的 YAML 为准/)
      ).not.toBeInTheDocument()
    })
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

    expect(studio.requestNodeFocus).toHaveBeenCalledWith('n_extract')
    await waitFor(() =>
      expect(
        screen.queryByText('草稿与 active revision 的差异')
      ).not.toBeInTheDocument()
    )
    // 节点在画布上：无「应用草稿后可定位」提示。
    expect(screen.queryByText(/应用草稿后可定位/)).not.toBeInTheDocument()
  })

  it('变更节点不在当前画布时不可定位：不选中、不关闭 dialog 并提示', async () => {
    // 草稿未「应用到编辑器」（或新增节点）时节点不在 studio.nodes 中：
    // 选中会被 useStudioNodeSelection 立即清掉，必须提前拦住并说明。
    const studio = makeStudio({ nodes: [] })
    renderCard(studio)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '查看 diff' }))
    })
    const nodeItem = await screen.findByText('提取: 节点配置值')
    fireEvent.click(nodeItem)

    expect(studio.requestNodeFocus).not.toHaveBeenCalled()
    expect(
      screen.getByText('草稿与 active revision 的差异')
    ).toBeInTheDocument()
    expect(
      screen.getByText('部分变更节点不在当前编辑器画布中，应用草稿后可定位')
    ).toBeInTheDocument()
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
