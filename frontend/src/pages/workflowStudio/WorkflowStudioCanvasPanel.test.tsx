import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioCanvasPanel } from './WorkflowStudioCanvasPanel'
import { makeStudioView, withStudioProviders } from './testStudioProviders'

vi.mock('../../components/dag/DagGraph', () => ({
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
      <WorkflowStudioCanvasPanel
        agentOpen
        onToggleAgent={() => {}}
        mobileActive
        replacedByDetail={false}
      />
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
        <WorkflowStudioCanvasPanel
          agentOpen
          onToggleAgent={() => {}}
          mobileActive
          replacedByDetail={false}
        />
      )
    )

    expect(
      screen.getByText('尚未发布 workflow，暂无 DAG 可展示。')
    ).toBeInTheDocument()
  })
})
