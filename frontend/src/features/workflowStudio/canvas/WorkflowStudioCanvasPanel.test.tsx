import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioCanvasPanel } from './WorkflowStudioCanvasPanel'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'

vi.mock('../../../components/dag/DagGraph', () => ({
  DagGraph: () => <div>DAG 画布 stub</div>,
}))

const baseStudio = {
  workflow: {
    key: 'demo',
    label: 'Demo',
    intake: { modes: [] },
    nodes: [],
    edges: [],
  },
  nodes: [],
  edges: [],
  selectedNodeKey: null,
  setSelectedNodeKey: vi.fn(),
}

function renderPanel(view: ReturnType<typeof makeStudioView>) {
  return render(
    withStudioProviders(
      baseStudio,
      view,
      <WorkflowStudioCanvasPanel mobileActive />
    )
  )
}

describe('WorkflowStudioCanvasPanel', () => {
  it('keeps the DAG as the single persistent canvas view', () => {
    renderPanel(makeStudioView())

    expect(screen.getByText('DAG 画布 stub')).toBeInTheDocument()
    // 不再有 DAG / YAML / 变更 三模式切换。
    expect(
      screen.queryByRole('group', { name: '画布模式' })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'open fullscreen DAG' })
    ).toBeInTheDocument()
    // #668：Agent 面板开关收敛到 appbar（CommandBar）唯一入口，
    // 画布工具条不再渲染。
    expect(
      screen.queryByRole('button', { name: 'toggle agent panel' })
    ).not.toBeInTheDocument()
  })

  it('opens the YAML editor dialog from the toolbar button', () => {
    const setYamlEditorOpen = vi.fn()
    renderPanel(makeStudioView({ setYamlEditorOpen }))

    fireEvent.click(screen.getByRole('button', { name: '编辑 YAML' }))

    expect(setYamlEditorOpen).toHaveBeenCalledWith(true)
  })

  it('renders the empty placeholder when no workflow and no ghost nodes', () => {
    render(
      withStudioProviders(
        { ...baseStudio, workflow: null },
        makeStudioView(),
        <WorkflowStudioCanvasPanel mobileActive />
      )
    )

    expect(
      screen.getByText('尚未发布 workflow，暂无 DAG 可展示。')
    ).toBeInTheDocument()
  })

  it('工具栏按浮动岛实测底边让位（#799 codex 复核 P2：不写死 top 常量）', () => {
    // 无岛（jsdom 布局恒 0）→ 回落安全距离 64。
    const { unmount } = render(
      withStudioProviders(
        baseStudio,
        makeStudioView(),
        <WorkflowStudioCanvasPanel mobileActive />
      )
    )
    expect(
      (document.querySelector('[data-canvas-toolbar]') as HTMLElement).style.top
    ).toBe('64px')
    unmount()

    // 假岛（底边 120）：工具栏 top = 120 + 8。
    const fakeIsland = document.createElement('div')
    fakeIsland.setAttribute('data-testid', 'studio-identity-island')
    fakeIsland.getBoundingClientRect = () => ({ bottom: 120 }) as DOMRect
    document.body.appendChild(fakeIsland)
    try {
      render(
        withStudioProviders(
          baseStudio,
          makeStudioView(),
          <WorkflowStudioCanvasPanel mobileActive />
        )
      )
      expect(
        (document.querySelector('[data-canvas-toolbar]') as HTMLElement).style
          .top
      ).toBe('128px')
    } finally {
      fakeIsland.remove()
    }
  })
})
