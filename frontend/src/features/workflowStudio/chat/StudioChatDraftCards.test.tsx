import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  AgentDefinitionDraftCard,
  NodeCodeDraftCard,
} from './StudioChatDraftCards'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'

/* #692：草稿卡类型化重做的钉子——MUI 线性图标 + 三类卡统一发布入口。
 * 图标断言走 MUI 渲染出的 svg role（aria-hidden，getByRole 查不到，
 * 用 container.querySelector 按类名定位 data-testid 之外的 svg 节点），
 * 发布按钮断言与 WorkflowDraftCard.test 同口径（role + 文案 + 禁用态
 * title 语义已在那边钉住，这里只钉「存在且走同一 requestPublish」）。 */

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

describe('AgentDefinitionDraftCard（#692）', () => {
  it('渲染 MUI 图标（非 emoji）与发布按钮，点击走 requestPublish', () => {
    const studio = makeStudio()
    const { container } = renderWithStudio(
      <AgentDefinitionDraftCard
        draft={{
          toolCallId: 'tc1',
          agentId: 'writer',
          capability: null,
          runtime: 'pi',
          skill: null,
        }}
      />,
      studio
    )
    expect(screen.getByText('Agent 定义草稿：writer')).toBeInTheDocument()
    // SmartToyOutlined 渲染为 svg；旧的 emoji 文本必须消失。
    expect(
      container.querySelector('svg[data-testid="SmartToyOutlinedIcon"]')
    ).not.toBeNull()
    expect(screen.queryByText(/🤖/)).not.toBeInTheDocument()
    const publish = screen.getByRole('button', { name: '发布新版本' })
    expect(publish).toBeEnabled()
    fireEvent.click(publish)
    expect(studio.requestPublish).toHaveBeenCalledTimes(1)
  })

  it('canPublish 为假时发布禁用（与 Workflow 卡同门控）', () => {
    renderWithStudio(
      <AgentDefinitionDraftCard
        draft={{
          toolCallId: 'tc1',
          agentId: 'writer',
          capability: null,
          runtime: null,
          skill: null,
        }}
      />,
      makeStudio({ canPublish: false })
    )
    expect(screen.getByRole('button', { name: '发布新版本' })).toBeDisabled()
  })
})

describe('NodeCodeDraftCard（#692）', () => {
  const draft = { toolCallId: 'tc2', nodeKey: 'fetch_url' }

  it('渲染 Code 图标与发布按钮，点击走 requestPublish', () => {
    const studio = makeStudio()
    const { container } = renderWithStudio(
      <NodeCodeDraftCard draft={draft} onSelectNode={vi.fn()} />,
      studio
    )
    expect(screen.getByText('节点代码草稿：fetch_url')).toBeInTheDocument()
    expect(
      container.querySelector('svg[data-testid="CodeOutlinedIcon"]')
    ).not.toBeNull()
    expect(screen.queryByText(/🧩/)).not.toBeInTheDocument()
    const publish = screen.getByRole('button', { name: '发布新版本' })
    expect(publish).toBeEnabled()
    fireEvent.click(publish)
    expect(studio.requestPublish).toHaveBeenCalledTimes(1)
  })

  it('无 onSelectNode（无定位链路）时仍有发布入口', () => {
    renderWithStudio(<NodeCodeDraftCard draft={draft} />, makeStudio())
    expect(screen.getByRole('button', { name: '发布新版本' })).toBeEnabled()
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
