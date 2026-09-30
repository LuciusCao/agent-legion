import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioCanvasPanel } from './WorkflowStudioCanvasPanel'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'

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
  // #799：岛挂在画布列内——补岛消费的 studio 字段空壳。
  revision: null,
  revisions: [],
  activeRevision: null,
  viewMode: 'draft',
  definitionYaml: 'key: demo\n',
  hasPreservedDraft: false,
  compareSummary: null,
  compareState: 'idle',
  actionState: 'idle',
  canSubmit: true,
  canPublish: true,
  selectedRevisionId: null,
  isLoadingRevision: false,
  revisionLoadError: null,
  selectRevision: vi.fn(),
  requestPublish: vi.fn(),
  resetDefinition: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

function renderPanel(view: ReturnType<typeof makeStudioView>) {
  return render(
    <MemoryRouter>
      {withStudioProviders(
        baseStudio,
        view,
        <WorkflowStudioCanvasPanel mobileActive />
      )}
    </MemoryRouter>
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
    // #668：Agent 面板开关收敛在顶栏体系（#799 起为画布列内的浮动岛），
    // 画布工具条不再有开关——断言限定工具条区域。
    expect(
      document.querySelector(
        '[data-canvas-toolbar] [aria-label="toggle agent panel"]'
      )
    ).toBeNull()
  })

  it('opens the YAML editor dialog from the toolbar button', () => {
    const setYamlEditorOpen = vi.fn()
    renderPanel(makeStudioView({ setYamlEditorOpen }))

    fireEvent.click(screen.getByRole('button', { name: '编辑 YAML' }))

    expect(setYamlEditorOpen).toHaveBeenCalledWith(true)
  })

  it('renders the empty placeholder when no workflow and no ghost nodes', () => {
    render(
      <MemoryRouter>
        {withStudioProviders(
          { ...baseStudio, workflow: null },
          makeStudioView(),
          <WorkflowStudioCanvasPanel mobileActive />
        )}
      </MemoryRouter>
    )

    expect(
      screen.getByText('尚未发布 workflow，暂无 DAG 可展示。')
    ).toBeInTheDocument()
  })

  it('工具栏按浮动岛实测底边让位（#799 codex 复核 P2：不写死 top 常量）', () => {
    // 无岛（jsdom 布局恒 0）→ 回落安全距离 64。
    const { unmount } = render(
      <MemoryRouter>
        {withStudioProviders(
          baseStudio,
          makeStudioView(),
          <WorkflowStudioCanvasPanel mobileActive />
        )}
      </MemoryRouter>
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
        <MemoryRouter>
          {withStudioProviders(
            baseStudio,
            makeStudioView(),
            <WorkflowStudioCanvasPanel mobileActive />
          )}
        </MemoryRouter>
      )
      expect(
        (document.querySelector('[data-canvas-toolbar]') as HTMLElement).style
          .top
      ).toBe('128px')
    } finally {
      fakeIsland.remove()
    }
  })

  it('双岛锚定在画布列内（codex 轮 2 P2：详情列打开时岛不越界）', () => {
    render(
      <MemoryRouter>
        {withStudioProviders(
          baseStudio,
          makeStudioView(),
          <WorkflowStudioCanvasPanel mobileActive />
        )}
      </MemoryRouter>
    )
    const canvas = document.querySelector('[data-mobile-panel="graph"]')
    expect(
      canvas?.querySelector('[data-testid="studio-identity-island"]')
    ).not.toBeNull()
    expect(
      canvas?.querySelector('[data-testid="studio-action-island"]')
    ).not.toBeNull()
  })

  it('岛尺寸变化（换行/变高）后工具栏让位重算；画布重挂后按新岛实测（codex 轮 2 P3）', async () => {
    const fakeIsland = document.createElement('div')
    fakeIsland.setAttribute('data-testid', 'studio-identity-island')
    let bottom = 120
    fakeIsland.getBoundingClientRect = () => ({ bottom }) as DOMRect
    document.body.appendChild(fakeIsland)
    const toolbar = () =>
      (document.querySelector('[data-canvas-toolbar]') as HTMLElement).style.top
    try {
      render(
        <MemoryRouter>
          {withStudioProviders(
            baseStudio,
            makeStudioView(),
            <WorkflowStudioCanvasPanel mobileActive />
          )}
        </MemoryRouter>
      )
      expect(toolbar()).toBe('128px')

      // 岛换行变高（底边 200）：resize 驱动重算 → top 208。
      bottom = 200
      fireEvent(window, new Event('resize'))
      await waitFor(() => expect(toolbar()).toBe('208px'))
    } finally {
      fakeIsland.remove()
    }
  })
})
