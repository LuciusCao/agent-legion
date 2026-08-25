import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioCanvasPanel } from './WorkflowStudioCanvasPanel'
import type { StudioCanvasMode } from './useWorkflowStudioPageView'
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
  definitionYaml: 'key: demo\n',
  setDefinitionYaml: vi.fn(),
  readOnly: false,
  validationMessage: '',
  validationErrors: [],
  compareErrors: null,
  compareSummary: null,
  compareState: 'idle' as const,
}

/** 受控 canvasMode 的有状态包装，贴近页面层用法。 */
function StatefulPanel() {
  const [mode, setMode] = useState<StudioCanvasMode>('dag')
  const view = makeStudioView({ canvasMode: mode, setCanvasMode: setMode })
  return withStudioProviders(
    baseStudio,
    view,
    <WorkflowStudioCanvasPanel
      agentOpen
      onToggleAgent={() => {}}
      mobileActive
      replacedByDetail={false}
    />
  )
}

describe('WorkflowStudioCanvasPanel', () => {
  it('switches between DAG, YAML and changes modes via the segmented control', () => {
    render(<StatefulPanel />)

    expect(screen.getByText('DAG 画布 stub')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'YAML' }))
    expect(screen.getByLabelText('工作流 YAML')).toBeInTheDocument()
    expect(screen.queryByText('DAG 画布 stub')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '变更' }))
    expect(screen.getByText('尚未运行校验。')).toBeInTheDocument()
    expect(screen.getByText('变更摘要')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'DAG 画布' }))
    expect(screen.getByText('DAG 画布 stub')).toBeInTheDocument()
  })

  it('only shows the DAG fullscreen button in DAG mode', () => {
    render(<StatefulPanel />)

    expect(
      screen.getByRole('button', { name: 'open fullscreen DAG' })
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'YAML' }))
    expect(
      screen.queryByRole('button', { name: 'open fullscreen DAG' })
    ).not.toBeInTheDocument()
  })
})
