import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClientProvider } from '@tanstack/react-query'
import {
  AgentDefinitionDraftCard,
  NodeCodeDraftCard,
} from './StudioChatDraftCards'
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
import { publishAgent } from '../../../api'
import { api } from '../../../api/core'

/* #692：草稿卡类型化重做的钉子——MUI 线性图标 + 按实体类型发布。
 * 图标断言走 MUI 渲染出的 svg data-testid（aria-hidden，getByRole 查
 * 不到）。发布断言（codex P1 修正后）：Agent/节点代码卡各调自己的实体
 * 发布端点（publishAgent / nodes/{key}/code/publish），成功 toast 走
 * uiStore、失效 studio 查询并进入「已发布」终态，失败内联展示——不
 * 再复用 workflow revision 的发布按钮。 */

vi.mock('../../../api', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  publishAgent: vi.fn(),
}))
vi.mock('../../../api/core', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  api: vi.fn(),
}))

const mockPublishAgent = vi.mocked(publishAgent)
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
    actionState: 'idle',
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
  useSettingStore.setState({ workspaceId: 'ws1' })
  useUiStore.setState({ toast: null })
})

afterEach(async () => {
  // 组件仍挂载时直改 zustand store 会触发 React 18 的 act 警告
  // （RTL cleanup 在本钩子之后才卸载树）——重置包进 act 消化更新，
  // 不用全文件级 expectConsoleError 吞掉未来用例的真实 act 缺陷。
  await act(async () => {
    useSettingStore.setState({ workspaceId: undefined })
  })
})

describe('AgentDefinitionDraftCard（#692）', () => {
  const draft = {
    toolCallId: 'tc1',
    agentId: 'writer',
    capability: null,
    runtime: 'pi',
    skill: null,
    status: 'completed',
  }

  it('渲染 MUI 图标（非 emoji）与实体发布按钮', () => {
    const { container } = renderWithStudio(
      <AgentDefinitionDraftCard draft={draft} />,
      makeStudio()
    )
    expect(screen.getByText('Agent 定义草稿：writer')).toBeInTheDocument()
    expect(
      container.querySelector('svg[data-testid="SmartToyOutlinedIcon"]')
    ).not.toBeNull()
    expect(screen.queryByText(/🤖/)).not.toBeInTheDocument()
    // codex P1 修正：按钮文案指明发布对象是 Agent 定义，不再是 workflow
    // revision 的「发布新版本」。
    expect(
      screen.getByRole('button', { name: '发布 Agent 定义' })
    ).toBeInTheDocument()
  })

  it('点击发布调 publishAgent 实体端点并 toast 成功', async () => {
    mockPublishAgent.mockResolvedValue({
      version: 2,
    } as Awaited<ReturnType<typeof publishAgent>>)
    renderWithStudio(<AgentDefinitionDraftCard draft={draft} />, makeStudio())

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
    })
    await waitFor(() =>
      expect(mockPublishAgent).toHaveBeenCalledWith('ws1', 'writer')
    )
    expect(mockApi).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('已发布')
    )
  })

  it('发布成功后按钮进入「已发布」终态且不可再点', async () => {
    mockPublishAgent.mockResolvedValue({
      version: 2,
    } as Awaited<ReturnType<typeof publishAgent>>)
    renderWithStudio(<AgentDefinitionDraftCard draft={draft} />, makeStudio())

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
    })
    const done = await screen.findByRole('button', { name: '已发布' })
    expect(done).toBeDisabled()
    fireEvent.click(done)
    expect(mockPublishAgent).toHaveBeenCalledTimes(1)
  })

  it('发布失败时按钮下方内联展示错误且不 toast 成功', async () => {
    mockPublishAgent.mockRejectedValue(new Error('capability 被占用'))
    renderWithStudio(<AgentDefinitionDraftCard draft={draft} />, makeStudio())

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
    })
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('capability 被占用')
    )
    expect(useUiStore.getState().toast).toBeNull()
  })

  it('404 no-draft 转成可行动的中文提示（R2 P2-2）', async () => {
    const notFound = Object.assign(new Error('no draft for agent writer'), {
      status: 404,
    })
    mockPublishAgent.mockRejectedValue(notFound)
    renderWithStudio(<AgentDefinitionDraftCard draft={draft} />, makeStudio())

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
    })
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        '没有待发布的草稿（可能刚已发布过）'
      )
    )
  })

  // R2 P2-1：pending/failed 的保存不开放发布——否则会把更早的旧草稿
  // 发布出去，用户误以为新定义已生效。
  it('来源 tool call 未完成时不渲染发布入口（pending/failed）', () => {
    for (const status of ['pending', 'failed']) {
      const { unmount } = renderWithStudio(
        <AgentDefinitionDraftCard draft={{ ...draft, status }} />,
        makeStudio()
      )
      expect(
        screen.queryByRole('button', { name: '发布 Agent 定义' })
      ).not.toBeInTheDocument()
      // 查看草稿不受影响：失败/等待中的草稿仍可查看。
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
          <AgentDefinitionDraftCard draft={draft} />
        )}
      </QueryClientProvider>
    )

    mockPublishAgent.mockResolvedValue({
      version: 2,
    } as Awaited<ReturnType<typeof publishAgent>>)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
    })
    await waitFor(() => expect(spy).toHaveBeenCalled())
    // 关键键在失效集里：Agent 目录 + 画布草稿（与 AgentEditor onChanged 等价）。
    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))
    expect(keys.some((key) => key.includes('studioAgentCatalog'))).toBe(true)
    expect(keys.some((key) => key.includes('workflowStudioDraft'))).toBe(true)
    spy.mockRestore()
  })
})

describe('NodeCodeDraftCard（#692）', () => {
  const draft = { toolCallId: 'tc2', nodeKey: 'fetch_url', status: 'completed' }

  it('渲染 Code 图标与实体发布按钮', () => {
    const { container } = renderWithStudio(
      <NodeCodeDraftCard draft={draft} onSelectNode={vi.fn()} />,
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
      <NodeCodeDraftCard draft={draft} onSelectNode={vi.fn()} />,
      makeStudio()
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布节点代码' }))
    })
    await waitFor(() =>
      expect(mockApi).toHaveBeenCalledWith(
        '/api/workspaces/ws1/nodes/fetch_url/code/publish',
        { method: 'POST' }
      )
    )
    expect(mockPublishAgent).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('新执行立即生效')
    )
  })

  it('发布失败时内联展示错误', async () => {
    mockApi.mockRejectedValue(new Error('网络错误') as never)
    renderWithStudio(
      <NodeCodeDraftCard draft={draft} onSelectNode={vi.fn()} />,
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
    renderWithStudio(<NodeCodeDraftCard draft={draft} />, makeStudio())
    expect(screen.getByRole('button', { name: '发布节点代码' })).toBeEnabled()
  })

  it('文案说明草稿语义：发布后新执行才使用', () => {
    renderWithStudio(
      <NodeCodeDraftCard draft={draft} onSelectNode={vi.fn()} />,
      makeStudio()
    )
    expect(
      screen.getByText(/已存为服务端草稿，发布后新执行才使用/)
    ).toBeInTheDocument()
  })
})
