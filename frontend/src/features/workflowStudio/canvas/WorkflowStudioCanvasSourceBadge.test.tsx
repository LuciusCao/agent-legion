import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useStudioState } from '../shared/studioStateContext'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { WorkflowStudioCanvasSourceBadge } from './WorkflowStudioCanvasSourceBadge'

vi.mock('../shared/studioStateContext', () => ({ useStudioState: vi.fn() }))

function makeSummary(
  ...types: Array<'added' | 'modified' | 'removed'>
): ChangeSummaryViewModel {
  return {
    createsRevision: true,
    riskLevel: 'info',
    severityLabel: '提示',
    nodeChanges: types.map((type, i) => ({
      type,
      nodeKey: `n${i}`,
      label: `n${i}`,
      nodeType: 'code',
      fields: [],
      severity: 'info',
    })),
    edgeChanges: [],
    intakeChanges: [],
    metadataChanges: [],
    riskFlags: [],
    changedNodeKeys: new Set(types.map((_, i) => `n${i}`)),
  }
}

function mockStudio(
  viewMode: 'draft' | 'revision',
  definitionYaml: string,
  extras: {
    dirty?: boolean
    compareSummary?: ChangeSummaryViewModel | null
  } = {}
) {
  vi.mocked(useStudioState).mockReturnValue({
    viewMode,
    definitionYaml,
    dirty: extras.dirty ?? false,
    compareSummary: extras.compareSummary ?? null,
  } as unknown as ReturnType<typeof useStudioState>)
}

describe('WorkflowStudioCanvasSourceBadge', () => {
  it('renders no chip in draft mode when the draft has no unpublished changes (#666)', () => {
    mockStudio('draft', 'key: demo\nnodes:\n  a:\n    capability: cap_a\n')

    const { container } = render(<WorkflowStudioCanvasSourceBadge />)

    expect(container).toBeEmptyDOMElement()
  })

  it('#804 定案：草稿有未发布变更时也不渲染角标 chip（由左岛状态 chip 唯一承接）', () => {
    mockStudio('draft', 'key: demo\nnodes:\n  a:\n    capability: cap_a\n', {
      dirty: true,
      compareSummary: makeSummary('added', 'modified'),
    })

    const { container } = render(<WorkflowStudioCanvasSourceBadge />)

    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByText(/草稿（未发布变更/)).toBeNull()
    expect(screen.queryByText('草稿（有未发布变更）')).toBeNull()
  })

  it('warns that the canvas shows the published version while the draft YAML is invalid', () => {
    mockStudio('draft', 'key: demo\nnodes: [broken', { dirty: true })

    render(<WorkflowStudioCanvasSourceBadge />)

    expect(
      screen.getByText('草稿 YAML 未完成解析，画布暂显示已发布版本')
    ).toBeInTheDocument()
  })

  it('warns when the draft YAML is syntactically valid but structurally malformed', () => {
    // `nodes:\n  review:`（值为 null）：形状残缺同样走回退提示，不 crash。
    mockStudio('draft', 'key: demo\nnodes:\n  review:\n')

    render(<WorkflowStudioCanvasSourceBadge />)

    expect(
      screen.getByText('草稿 YAML 未完成解析，画布暂显示已发布版本')
    ).toBeInTheDocument()
  })

  it('轮 6 H4：compare 传输失败出「草稿对比失败」警示 chip，点击重试', () => {
    const retryCompare = vi.fn()
    vi.mocked(useStudioState).mockReturnValue({
      viewMode: 'draft',
      definitionYaml: 'key: demo\nnodes:\n  a:\n    capability: cap_a\n',
      dirty: true,
      compareSummary: null,
      compareState: 'error',
      retryCompare,
    } as unknown as ReturnType<typeof useStudioState>)

    render(<WorkflowStudioCanvasSourceBadge />)

    fireEvent.click(screen.getByText('草稿对比失败'))
    expect(retryCompare).toHaveBeenCalledOnce()
  })

  it('renders nothing in revision mode (the 只读 vN chip already covers it)', () => {
    mockStudio('revision', 'key: wf\nlabel: Old\n')

    const { container } = render(<WorkflowStudioCanvasSourceBadge />)

    expect(container).toBeEmptyDOMElement()
  })
})
