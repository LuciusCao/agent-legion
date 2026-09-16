import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AgentDefinitionDraftCard,
  NodeCodeDraftCard,
} from './StudioChatDraftCards'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'
import { useSettingStore } from '../../../stores/settingStore'
import { useUiStore } from '../../../stores/uiStore'
import { publishAgent } from '../../../api'
import { api } from '../../../api/core'
import { expectConsoleError } from '../../../test-setup'

/* #692：草稿卡类型化重做的钉子——MUI 线性图标 + 按实体类型发布。
 * 图标断言走 MUI 渲染出的 svg data-testid（aria-hidden，getByRole 查
 * 不到）。发布断言（codex P1 修正后）：Agent/节点代码卡各调自己的实体
 * 发布端点（publishAgent / nodes/{key}/code/publish），成功 toast 走
 * uiStore、失败内联展示——不再复用 workflow revision 的发布按钮。 */

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
  return render(withStudioProviders(studio, makeStudioView(), ui))
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
  // zustand store 直改会令已挂载组件的订阅在 act 外更新（React 18 报
  // console.error 级 act 警告）——本套件的 beforeEach 在 render 前重置
  // store，警告来自前一个用例卸载竞态，登记吸收（PreviewPanelSection
  // .test 的 warn 级同族）。
  expectConsoleError(/not wrapped in act/)
  useSettingStore.setState({ workspaceId: 'ws1' })
  useUiStore.setState({ toast: null })
})

afterEach(() => {
  useSettingStore.setState({ workspaceId: undefined })
})

describe('AgentDefinitionDraftCard（#692）', () => {
  const draft = {
    toolCallId: 'tc1',
    agentId: 'writer',
    capability: null,
    runtime: 'pi',
    skill: null,
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
})

describe('NodeCodeDraftCard（#692）', () => {
  const draft = { toolCallId: 'tc2', nodeKey: 'fetch_url' }

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
