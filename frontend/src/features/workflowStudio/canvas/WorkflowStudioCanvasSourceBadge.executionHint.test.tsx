import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useStudioState } from '../shared/studioStateContext'
import { WorkflowStudioExecutionHint } from './WorkflowStudioCanvasSourceBadge.executionHint'
import type { WorkflowDefinitionRecord } from '../../../types'

vi.mock('../shared/studioStateContext', () => ({ useStudioState: vi.fn() }))

const HINT = '未配置顶层 execution 默认，Agent 节点需各自配齐 provider / model'

function makeWorkflow(nodeTypes: string[]): WorkflowDefinitionRecord {
  return {
    key: 'wf',
    label: 'WF',
    intake: { modes: [] },
    edges: [],
    nodes: nodeTypes.map((type, i) => ({
      key: `n${i}`,
      label: `n${i}`,
      capability: 'cap',
      after: [],
      inputs: [],
      outputs: [],
      node_type: type,
    })),
  } as unknown as WorkflowDefinitionRecord
}

function mockStudio(
  viewMode: 'draft' | 'revision',
  definitionYaml: string,
  workflow: WorkflowDefinitionRecord | null
) {
  vi.mocked(useStudioState).mockReturnValue({
    viewMode,
    definitionYaml,
    workflow,
  } as unknown as ReturnType<typeof useStudioState>)
}

describe('WorkflowStudioExecutionHint', () => {
  it('shows the hint when agent nodes exist without top-level execution defaults', () => {
    mockStudio(
      'draft',
      'key: wf\nnodes:\n  review:\n    type: agent\n    capability: review\n',
      makeWorkflow(['agent'])
    )

    render(<WorkflowStudioExecutionHint />)

    expect(screen.getByText(HINT)).toBeInTheDocument()
  })

  it('stays silent when the top-level execution block covers the fallback', () => {
    mockStudio(
      'draft',
      'key: wf\nexecution:\n  provider: openai\n  model: gpt-5\nnodes:\n  review:\n    type: agent\n',
      makeWorkflow(['agent'])
    )

    render(<WorkflowStudioExecutionHint />)

    expect(screen.queryByText(HINT)).not.toBeInTheDocument()
  })

  it('stays silent for pure code workflows', () => {
    mockStudio(
      'draft',
      'key: wf\nnodes:\n  fetch:\n    capability: fetch\n',
      makeWorkflow(['code'])
    )

    render(<WorkflowStudioExecutionHint />)

    expect(screen.queryByText(HINT)).not.toBeInTheDocument()
  })

  it('stays silent while the draft YAML is mid-edit invalid (canvas fell back)', () => {
    // 解析失败时画布回退 published，其顶层块形状不可得——不猜、不提示。
    mockStudio('draft', 'key: wf\nnodes: [broken', makeWorkflow(['agent']))

    render(<WorkflowStudioExecutionHint />)

    expect(screen.queryByText(HINT)).not.toBeInTheDocument()
  })

  it('also shows the hint when viewing a revision without top-level defaults', () => {
    mockStudio(
      'revision',
      'key: wf\nnodes:\n  review:\n    type: agent\n',
      makeWorkflow(['agent'])
    )

    render(<WorkflowStudioExecutionHint />)

    expect(screen.getByText(HINT)).toBeInTheDocument()
  })
})
