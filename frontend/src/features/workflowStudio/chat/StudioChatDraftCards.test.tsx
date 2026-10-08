import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClientProvider } from '@tanstack/react-query'
import { NodeCodeDraftCard } from './StudioChatDraftCards'
import type { NodeCodeDraftView } from './studioChatMessages'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'
import {
  TestQueryProvider,
  createTestQueryClient,
} from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import { useUiStore } from '../../../stores/uiStore'
import { api } from '../../../api/core'

/* #692：草稿卡类型化重做的钉子——MUI 线性图标 + 实体发布。图标断言走
 * MUI 渲染出的 svg data-testid（aria-hidden，getByRole 查不到）。发布断言
 * （codex P1 修正后）：节点代码卡调自己的实体发布端点
 * （nodes/{key}/code/publish），请求携带卡片的 draftHash 作 expected_hash
 * 交服务端原子核对。成功 toast 走 uiStore、失效 studio 查询并进入「已发布」
 * 终态，失败内联展示——不复用 workflow revision 的发布按钮。 */

vi.mock('../../../api/core', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

function renderWithStudio(
  ui: React.ReactNode,
  studio: Record<string, unknown>
) {
  // EntityDraftPublishButton 用 useQueryClient 失效查询，测试树需挂
  // QueryClientProvider（每树独立 client，无重试无缓存）。
  return render(
    <TestQueryProvider>
      {withStudioProviders(studio, makeStudioView(), ui)}
    </TestQueryProvider>
  )
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
    definitionYaml: '',
    nodes: [],
    focusNonce: 0,
    requestNodeFocus: vi.fn(),
    requestPublish: vi.fn(),
    setSelectedNodeKey: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  useUiStore.setState({ toast: null })
})

// #1079（#440 P3b）：Agent 定义草稿卡已下线，发布门控的回归钉子迁到
// 节点代码卡（同一 DraftPublishAction / EntityDraftPublishButton 链路）。
describe('节点代码卡的发布门控（#692）', () => {
  const draft: NodeCodeDraftView = {
    toolCallId: 'tc2',
    nodeKey: 'fetch_url',
    status: 'completed',
    draftHash: 'code-hash-a',
    saveFailed: false,
  }
  const publishUrl = '/api/workspaces/ws1/nodes/fetch_url/code/publish'

  function renderCard(overrides: Partial<NodeCodeDraftView> = {}, ws = 'ws1') {
    return renderWithStudio(
      <NodeCodeDraftCard
        draft={{ ...draft, ...overrides }}
        workspaceId={ws}
        onSelectNode={vi.fn()}
      />,
      makeStudio()
    )
  }

  it('发布成功后按钮进入「已发布」终态且不可再点', async () => {
    mockApi.mockResolvedValue({} as never)
    renderCard()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布节点代码' }))
    })
    const done = await screen.findByRole('button', { name: '已发布' })
    expect(done).toBeDisabled()
    fireEvent.click(done)
    expect(mockApi).toHaveBeenCalledTimes(1)
  })

  it('404 no-draft 转成可行动的中文提示（R2 P2-2）', async () => {
    const notFound = Object.assign(new Error('no draft for node fetch_url'), {
      status: 404,
    })
    mockApi.mockRejectedValue(notFound as never)
    renderCard()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布节点代码' }))
    })
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        '没有待发布的草稿（可能刚已发布过）'
      )
    )
    expect(useUiStore.getState().toast).toBeNull()
  })

  // R5 P2-1：MCP ToolClient 对非 2xx 返回 "HTTP 4xx: …" 文本而不抛异常，
  // tool call 在协议层仍 completed——仅 status 门挡不住，会静默发布旧
  // 草稿。
  it('HTTP 层失败的保存（completed + 失败文本）不渲染发布入口', () => {
    renderCard({ saveFailed: true, draftHash: null })
    expect(
      screen.queryByRole('button', { name: '发布节点代码' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看草稿' })).toBeEnabled()
  })

  // R2 P2-1：pending/failed 的保存不开放发布——否则会把更早的旧草稿
  // 发布出去，用户误以为新代码已生效。
  it('来源 tool call 未完成时不渲染发布入口（pending/failed）', () => {
    for (const status of ['pending', 'failed']) {
      const { unmount } = renderCard({ status })
      expect(
        screen.queryByRole('button', { name: '发布节点代码' })
      ).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: '查看草稿' })).toBeEnabled()
      unmount()
    }
  })

  it('发布成功后失效 studio 查询（invalidation 接线，R2 P3-4）', async () => {
    const client = createTestQueryClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    render(
      <QueryClientProvider client={client}>
        {withStudioProviders(
          makeStudio(),
          makeStudioView(),
          <NodeCodeDraftCard draft={draft} workspaceId="ws1" />
        )}
      </QueryClientProvider>
    )

    mockApi.mockResolvedValue({} as never)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布节点代码' }))
    })
    await waitFor(() => expect(spy).toHaveBeenCalled())
    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))
    expect(keys.some((key) => key.includes('workflowStudioDraft'))).toBe(true)
    spy.mockRestore()
  })

  it('服务端 409（草稿被覆盖）：内联提示刷新，无成功 toast，按钮可重试', async () => {
    const conflict = Object.assign(
      new Error('draft hash mismatch for node fetch_url'),
      { status: 409 }
    )
    mockApi.mockRejectedValue(conflict as never)
    renderCard()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布节点代码' }))
    })
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        '草稿已被其他会话或编辑器更新，请在检查器面板中从最新草稿发布'
      )
    )
    expect(useUiStore.getState().toast).toBeNull()
    expect(screen.getByRole('button', { name: '发布节点代码' })).toBeEnabled()
  })

  // codex P1 第四轮：draftHash null 的旧转录卡不渲染发布入口——无法
  // 参与原子核对的发布在草稿被覆盖时会静默发出别人的内容。
  it('旧转录 draftHash 为 null：不渲染发布入口，提示走检查器面板', () => {
    renderCard({ draftHash: null })
    expect(
      screen.queryByRole('button', { name: '发布节点代码' })
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(/旧转录无法验证草稿版本，请在检查器面板中发布/)
    ).toBeInTheDocument()
  })

  // R4 P1：workspaceId 来自 prop（路由/调用方），不读全局 store。
  it('发布调用使用 prop 的 workspaceId（store 设冲突值也以 prop 为准）', async () => {
    mockApi.mockResolvedValue({} as never)
    useSettingStore.setState({ workspaceId: 'ws-store' })
    renderCard({}, 'ws-job-context')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布节点代码' }))
    })
    await waitFor(() =>
      expect(mockApi).toHaveBeenCalledWith(
        '/api/workspaces/ws-job-context/nodes/fetch_url/code/publish',
        {
          method: 'POST',
          body: JSON.stringify({ expected_hash: 'code-hash-a' }),
        }
      )
    )
    expect(mockApi).not.toHaveBeenCalledWith(publishUrl, expect.anything())
  })
})

describe('NodeCodeDraftCard（#692）', () => {
  const draft = {
    toolCallId: 'tc2',
    nodeKey: 'fetch_url',
    status: 'completed',
    draftHash: 'code-hash-a',
    saveFailed: false,
  }

  it('渲染 Code 图标与实体发布按钮', () => {
    const { container } = renderWithStudio(
      <NodeCodeDraftCard
        draft={draft}
        workspaceId="ws1"
        onSelectNode={vi.fn()}
      />,
      makeStudio()
    )
    expect(screen.getByText('节点代码草稿：fetch_url')).toBeInTheDocument()
    expect(
      container.querySelector('svg[data-testid="CodeOutlinedIcon"]')
    ).not.toBeNull()
    expect(screen.queryByText(/🧩/)).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '发布节点代码' })
    ).toBeInTheDocument()
  })

  // R3 P2-2：草稿状态文案按来源 status 分支——failed/pending 的保存说
  // 「已存为服务端草稿」是假话（草稿未落库，服务端还是上一份）。
  it('草稿状态文案按来源 tool call 状态分支', () => {
    const cases = [
      ['completed', /已存为服务端草稿，发布后新执行才使用/],
      ['failed', /本次保存失败，草稿未更新/],
      ['pending', /保存中…/],
    ] as const
    for (const [status, pattern] of cases) {
      const { unmount } = renderWithStudio(
        <NodeCodeDraftCard
          draft={{ ...draft, status }}
          workspaceId="ws1"
          onSelectNode={vi.fn()}
        />,
        makeStudio()
      )
      expect(screen.getByText(pattern)).toBeInTheDocument()
      unmount()
    }
  })

  it('点击发布调节点代码 publish 端点并 toast 成功', async () => {
    mockApi.mockResolvedValue({} as never)
    renderWithStudio(
      <NodeCodeDraftCard
        draft={draft}
        workspaceId="ws1"
        onSelectNode={vi.fn()}
      />,
      makeStudio()
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布节点代码' }))
    })
    await waitFor(() =>
      expect(mockApi).toHaveBeenCalledWith(
        '/api/workspaces/ws1/nodes/fetch_url/code/publish',
        {
          method: 'POST',
          body: JSON.stringify({ expected_hash: 'code-hash-a' }),
        }
      )
    )
    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('新执行立即生效')
    )
  })

  it('发布失败时内联展示错误', async () => {
    mockApi.mockRejectedValue(new Error('网络错误') as never)
    renderWithStudio(
      <NodeCodeDraftCard
        draft={draft}
        workspaceId="ws1"
        onSelectNode={vi.fn()}
      />,
      makeStudio()
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布节点代码' }))
    })
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('网络错误')
    )
  })

  it('无 onSelectNode（无定位链路）时仍有发布入口', () => {
    renderWithStudio(
      <NodeCodeDraftCard draft={draft} workspaceId="ws1" />,
      makeStudio()
    )
    expect(screen.getByRole('button', { name: '发布节点代码' })).toBeEnabled()
  })

  it('文案说明草稿语义：发布后新执行才使用', () => {
    renderWithStudio(
      <NodeCodeDraftCard
        draft={draft}
        workspaceId="ws1"
        onSelectNode={vi.fn()}
      />,
      makeStudio()
    )
    expect(
      screen.getByText(/已存为服务端草稿，发布后新执行才使用/)
    ).toBeInTheDocument()
  })
})
