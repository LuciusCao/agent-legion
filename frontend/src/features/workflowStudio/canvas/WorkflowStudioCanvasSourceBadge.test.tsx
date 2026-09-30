import { render, screen } from '@testing-library/react'
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

  it('renders nothing in revision mode (the 只读 vN chip already covers it)', () => {
    mockStudio('revision', 'key: wf\nlabel: Old\n')

    const { container } = render(<WorkflowStudioCanvasSourceBadge />)

    expect(container).toBeEmptyDOMElement()
  })

  it('keeps the execution hint when top-level defaults are missing (#333)', () => {
    vi.mocked(useStudioState).mockReturnValue({
      viewMode: 'draft',
      definitionYaml: 'key: demo\nnodes:\n  a:\n    type: agent\n',
      dirty: true,
      compareSummary: makeSummary('modified'),
      workflow: {
        key: 'demo',
        label: 'Demo',
        intake: { modes: [] },
        edges: [],
        nodes: [
          {
            key: 'a',
            label: 'a',
            capability: 'cap_a',
            after: [],
            inputs: [],
            outputs: [],
            node_type: 'agent',
          },
        ],
      },
    } as unknown as ReturnType<typeof useStudioState>)

    render(<WorkflowStudioCanvasSourceBadge />)

    // 草稿 chip 已去重移除，execution 提示保留。
    expect(screen.queryByText(/草稿（未发布变更/)).toBeNull()
    expect(
      screen.getByText(
        '未配置顶层 execution 默认，Agent 节点需各自配齐 provider / model'
      )
    ).toBeInTheDocument()
  })
})
