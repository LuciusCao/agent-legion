import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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
import { fetchAgentVersions, publishAgent } from '../../../api'
import { api } from '../../../api/core'

/* #692：草稿卡类型化重做的钉子——MUI 线性图标 + 按实体类型发布。
 * 图标断言走 MUI 渲染出的 svg data-testid（aria-hidden，getByRole 查
 * 不到）。发布断言（codex P1 修正后）：Agent/节点代码卡各调自己的实体
 * 发布端点（publishAgent / nodes/{key}/code/publish），发布前按卡片
 * 携带的 draftHash 与服务端当前草稿比对（codex P1 第三轮：实体是
 * workspace 级状态，本会话的「最新」可能已被覆盖）；一致才发。成功
 * toast 走 uiStore、失效 studio 查询并进入「已发布」终态，失败内联
 * 展示——不再复用 workflow revision 的发布按钮。 */

vi.mock('../../../api', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  publishAgent: vi.fn(),
  fetchAgentVersions: vi.fn(),
}))
vi.mock('../../../api/core', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  api: vi.fn(),
}))

const mockPublishAgent = vi.mocked(publishAgent)
const mockFetchAgentVersions = vi.mocked(fetchAgentVersions)
const mockApi = vi.mocked(api)

/** agent versions 响应：首个 draft 行即当前草稿（列表 version 降序）。 */
function agentVersions(draftHash: string | null) {
  return {
    versions: [
      ...(draftHash
        ? [
            {
              agent_id: 'writer',
              created_at: '2026-01-01T00:00:00Z',
              created_by: 'u1',
              definition_hash: draftHash,
              id: 'v2',
              status: 'draft' as const,
              version: 2,
            },
          ]
        : []),
      {
        agent_id: 'writer',
        created_at: '2026-01-01T00:00:00Z',
        created_by: 'u1',
        definition_hash: 'published-hash',
        id: 'v1',
        status: 'published' as const,
        version: 1,
      },
    ],
  }
}

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
  useUiStore.setState({ toast: null })
})

describe('AgentDefinitionDraftCard（#692）', () => {
  const draft = {
    toolCallId: 'tc1',
    agentId: 'writer',
    capability: null,
    runtime: 'pi',
    skill: null,
    status: 'completed',
    draftHash: null,
    saveFailed: false,
  }

  it('渲染 MUI 图标（非 emoji）与实体发布按钮', () => {
    const { container } = renderWithStudio(
      <AgentDefinitionDraftCard draft={draft} workspaceId="ws1" />,
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
    renderWithStudio(
      <AgentDefinitionDraftCard draft={draft} workspaceId="ws1" />,
      makeStudio()
    )

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
    renderWithStudio(
      <AgentDefinitionDraftCard draft={draft} workspaceId="ws1" />,
      makeStudio()
    )

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
    renderWithStudio(
      <AgentDefinitionDraftCard draft={draft} workspaceId="ws1" />,
      makeStudio()
    )

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
    renderWithStudio(
      <AgentDefinitionDraftCard draft={draft} workspaceId="ws1" />,
      makeStudio()
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
    })
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        '没有待发布的草稿（可能刚已发布过）'
      )
    )
  })

  // R5 P2-1：MCP ToolClient 对非 2xx 返回 "HTTP 4xx: …" 文本而不抛异常，
  // tool call 在协议层仍 completed——仅 status 门挡不住，会静默发布旧
  // 草稿（无 draftHash 也跳过核对与残窗警告）。
  it('HTTP 层失败的保存（completed + 失败文本）不渲染发布入口', () => {
    renderWithStudio(
      <AgentDefinitionDraftCard
        draft={{ ...draft, saveFailed: true, draftHash: null }}
        workspaceId="ws1"
      />,
      makeStudio()
    )
    expect(
      screen.queryByRole('button', { name: '发布 Agent 定义' })
    ).not.toBeInTheDocument()
    // 查看草稿不受影响。
    expect(screen.getByRole('button', { name: '查看草稿' })).toBeEnabled()
  })

  // R2 P2-1：pending/failed 的保存不开放发布——否则会把更早的旧草稿
  // 发布出去，用户误以为新定义已生效。
  it('来源 tool call 未完成时不渲染发布入口（pending/failed）', () => {
    for (const status of ['pending', 'failed']) {
      const { unmount } = renderWithStudio(
        <AgentDefinitionDraftCard
          draft={{ ...draft, status }}
          workspaceId="ws1"
        />,
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
          <AgentDefinitionDraftCard draft={draft} workspaceId="ws1" />
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

  // codex P1 第三轮：发布前核对服务端草稿身份。实体是 workspace 级状态，
  // 本会话保存后其他会话（或用户在编辑器）可以覆盖——卡片 hash 与服务端
  // 当前草稿一致才发。
  describe('发布前草稿身份核对（codex P1 第三轮）', () => {
    it('服务端当前草稿 hash 一致：正常发布', async () => {
      mockFetchAgentVersions.mockResolvedValue(
        agentVersions('hash-a') as Awaited<
          ReturnType<typeof fetchAgentVersions>
        >
      )
      mockPublishAgent.mockResolvedValue({
        version: 2,
      } as Awaited<ReturnType<typeof publishAgent>>)
      renderWithStudio(
        <AgentDefinitionDraftCard
          draft={{ ...draft, draftHash: 'hash-a' }}
          workspaceId="ws1"
        />,
        makeStudio()
      )

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
      })
      await waitFor(() =>
        expect(mockFetchAgentVersions).toHaveBeenCalledWith('ws1', 'writer')
      )
      await waitFor(() =>
        expect(mockPublishAgent).toHaveBeenCalledWith('ws1', 'writer')
      )
    })

    it('服务端草稿已被其他会话覆盖（hash 不一致）：拦截并提示，不发布', async () => {
      mockFetchAgentVersions.mockResolvedValue(
        agentVersions('hash-b') as Awaited<
          ReturnType<typeof fetchAgentVersions>
        >
      )
      renderWithStudio(
        <AgentDefinitionDraftCard
          draft={{ ...draft, draftHash: 'hash-a' }}
          workspaceId="ws1"
        />,
        makeStudio()
      )

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
      })
      await waitFor(() =>
        expect(screen.getByRole('alert')).toHaveTextContent(
          '草稿已被其他会话或编辑器更新，当前卡片不再对应最新草稿'
        )
      )
      expect(mockPublishAgent).not.toHaveBeenCalled()
      // 按钮回到可点态（非终态）：提示用户去刷新/查看新草稿。
      expect(
        screen.getByRole('button', { name: '发布 Agent 定义' })
      ).toBeEnabled()
    })

    it('服务端已无草稿（draft 行缺失）：拦截并提示刷新，不发布', async () => {
      mockFetchAgentVersions.mockResolvedValue(
        agentVersions(null) as Awaited<ReturnType<typeof fetchAgentVersions>>
      )
      renderWithStudio(
        <AgentDefinitionDraftCard
          draft={{ ...draft, draftHash: 'hash-a' }}
          workspaceId="ws1"
        />,
        makeStudio()
      )

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
      })
      await waitFor(() =>
        expect(screen.getByRole('alert')).toHaveTextContent(
          '服务端草稿已变更（可能已被发布或覆盖），请刷新后重试'
        )
      )
      expect(mockPublishAgent).not.toHaveBeenCalled()
    })

    it('旧转录 draftHash 为 null：跳过核对直接发布（404 兜底）', async () => {
      mockPublishAgent.mockResolvedValue({
        version: 2,
      } as Awaited<ReturnType<typeof publishAgent>>)
      renderWithStudio(
        <AgentDefinitionDraftCard
          draft={{ ...draft, draftHash: null }}
          workspaceId="ws1"
        />,
        makeStudio()
      )

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
      })
      await waitFor(() =>
        expect(mockPublishAgent).toHaveBeenCalledWith('ws1', 'writer')
      )
      expect(mockFetchAgentVersions).not.toHaveBeenCalled()
    })

    it('节点代码卡同样核对：versions 首个 draft 行的 code_hash 不一致则拦截', async () => {
      mockApi.mockResolvedValue({
        versions: [
          {
            change_note: null,
            code_hash: 'code-hash-b',
            created_at: '2026-01-01T00:00:00Z',
            created_by: 'u1',
            id: 'v3',
            published_at: null,
            status: 'draft',
            version: 3,
          },
        ],
      } as never)
      // NodeCode 卡的 fixture 形状（agent 组的 draft 无 nodeKey）。
      const nodeDraft = {
        toolCallId: 'tc2',
        nodeKey: 'fetch_url',
        status: 'completed',
        draftHash: 'code-hash-a',
        saveFailed: false,
      }
      renderWithStudio(
        <NodeCodeDraftCard
          draft={nodeDraft}
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
          '/api/workspaces/ws1/nodes/fetch_url/code/versions'
        )
      )
      await waitFor(() =>
        expect(screen.getByRole('alert')).toHaveTextContent(
          '草稿已被其他会话或编辑器更新，当前卡片不再对应最新草稿'
        )
      )
      // publish 端点未被调用（唯一一次 api 调用是 versions 读取）。
      expect(mockApi).toHaveBeenCalledTimes(1)
    })

    // R4 P2 残窗检测：核对通过后、发布落地前被覆盖——发布响应 hash 与
    // 卡片不一致时，成功 toast 之外必须再出一条警告。
    it('发布响应 hash 与卡片不一致：警告 toast 提示内容已被覆盖', async () => {
      mockFetchAgentVersions.mockResolvedValue(
        agentVersions('hash-a') as Awaited<
          ReturnType<typeof fetchAgentVersions>
        >
      )
      mockPublishAgent.mockResolvedValue({
        definition_hash: 'hash-b',
      } as Awaited<ReturnType<typeof publishAgent>>)
      renderWithStudio(
        <AgentDefinitionDraftCard
          draft={{ ...draft, draftHash: 'hash-a' }}
          workspaceId="ws1"
        />,
        makeStudio()
      )

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
      })
      await waitFor(() =>
        expect(useUiStore.getState().toast?.message).toContain(
          '发布的内容已非卡片生成时的版本'
        )
      )
      expect(useUiStore.getState().toast?.type).toBe('error')
    })

    // R4 P1：workspaceId 来自 prop（路由/调用方），不读全局 store——
    // job 排查/定制预览载体在别的 workspace 下渲染时不得发到 store 里
    // 的旧 workspace。
    it('发布调用使用 prop 的 workspaceId（store 设冲突值也以 prop 为准）', async () => {
      mockPublishAgent.mockResolvedValue({
        definition_hash: 'hash-a',
      } as Awaited<ReturnType<typeof publishAgent>>)
      // P3-2（R5）：store 设冲突值——若未来有人加回 store 兜底，此用例
      // 必红（旧代码会因读 store 发布到 ws-store）。
      useSettingStore.setState({ workspaceId: 'ws-store' })
      renderWithStudio(
        <AgentDefinitionDraftCard
          draft={{ ...draft, draftHash: null }}
          workspaceId="ws-job-context"
        />,
        makeStudio()
      )

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
      })
      await waitFor(() =>
        expect(mockPublishAgent).toHaveBeenCalledWith(
          'ws-job-context',
          'writer'
        )
      )
    })

    // R4 P3-1：versions 读取失败（含 404 实体不存在）与发布 404 的文案
    // 区分——读取失败不说「没有待发布的草稿」。
    it('versions 读取失败：提示网络/读取问题而非无草稿', async () => {
      mockFetchAgentVersions.mockRejectedValue(new Error('network down'))
      renderWithStudio(
        <AgentDefinitionDraftCard
          draft={{ ...draft, draftHash: 'hash-a' }}
          workspaceId="ws1"
        />,
        makeStudio()
      )

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '发布 Agent 定义' }))
      })
      await waitFor(() =>
        expect(screen.getByRole('alert')).toHaveTextContent(
          '无法读取服务端草稿状态，请检查网络后重试'
        )
      )
      expect(mockPublishAgent).not.toHaveBeenCalled()
    })
  })
})

describe('NodeCodeDraftCard（#692）', () => {
  const draft = {
    toolCallId: 'tc2',
    nodeKey: 'fetch_url',
    status: 'completed',
    draftHash: null,
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
