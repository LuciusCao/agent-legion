import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useStudioState } from '../shared/studioStateContext'
import { WorkflowStudioCanvasSourceBadge } from './WorkflowStudioCanvasSourceBadge'

vi.mock('../shared/studioStateContext', () => ({ useStudioState: vi.fn() }))

function mockStudio(viewMode: 'draft' | 'revision', definitionYaml: string) {
  vi.mocked(useStudioState).mockReturnValue({
    viewMode,
    definitionYaml,
  } as unknown as ReturnType<typeof useStudioState>)
}

describe('WorkflowStudioCanvasSourceBadge', () => {
  it('marks the canvas as draft (unpublished) in draft mode with valid YAML', () => {
    mockStudio('draft', 'key: demo\nnodes:\n  a:\n    capability: cap_a\n')

    render(<WorkflowStudioCanvasSourceBadge />)

    expect(screen.getByText('草稿（未发布）')).toBeInTheDocument()
  })

  it('warns that the canvas shows the published version while the draft YAML is invalid', () => {
    mockStudio('draft', 'key: demo\nnodes: [broken')

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

  it('accompanies the draft chip with the execution hint when top-level defaults are missing (#333)', () => {
    vi.mocked(useStudioState).mockReturnValue({
      viewMode: 'draft',
      definitionYaml: 'key: demo\nnodes:\n  a:\n    type: agent\n',
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

    expect(screen.getByText('草稿（未发布）')).toBeInTheDocument()
    expect(
      screen.getByText(
        '未配置顶层 execution 默认，Agent 节点需各自配齐 provider / model'
      )
    ).toBeInTheDocument()
  })
})
