import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { WorkflowNodeInspectorSections } from './WorkflowNodeInspectorSections'
import { api } from '../../api'
import { useSettingStore } from '../../stores/settingStore'
import type { WorkflowNodeRecord } from '../../types'

vi.mock('../../api', () => ({
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

const startNode: WorkflowNodeRecord = {
  key: '_start',
  label: 'Start',
  capability: '',
  after: [],
  inputs: [],
  outputs: [],
  node_type: 'start',
  accepted_item_types: ['material', 'ref'],
}

const startYaml = `key: demo
nodes:
  _start:
    type: start
    accepted_item_types:
      - material
      - ref
`

function renderSections(
  node: WorkflowNodeRecord,
  options?: {
    readOnly?: boolean
    definitionYaml?: string
    setDefinitionYaml?: (value: string) => void
  }
) {
  return render(
    <WorkflowNodeInspectorSections
      details={{ node, incoming: [], outgoing: [] }}
      agentCatalog={[]}
      definitionYaml={options?.definitionYaml ?? startYaml}
      setDefinitionYaml={options?.setDefinitionYaml ?? (() => {})}
      workflowKey="demo_workflow"
      readOnly={options?.readOnly}
    />
  )
}

describe('WorkflowNodeInspectorSections for a start node', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useSettingStore.setState({ workspaceId: 'default' })
  })

  it('shows the read-only entry contract instead of code/execution editors', () => {
    renderSections(startNode, { readOnly: true })

    expect(screen.getByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.getByText(/上传文件、外部平台内容/)).toBeInTheDocument()
    // The capability/execution/code editors do not apply to a node that
    // never executes (the backend 404s its node-code endpoints).
    expect(screen.queryByLabelText('节点代码')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('节点执行能力')).not.toBeInTheDocument()
    // No node-code fetch for a start node.
    expect(mockApi).not.toHaveBeenCalled()
    // Structure info stays available.
    expect(screen.getByText('依赖关系')).toBeInTheDocument()
    // readOnly 下不渲染可编辑的 checkbox。
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('renders the three accepted_item_types checkboxes when editable', () => {
    renderSections(startNode)

    expect(screen.getByRole('checkbox', { name: /上传文件/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /外部平台内容/ })).toBeChecked()
    expect(
      screen.getByRole('checkbox', { name: /整个文件夹/ })
    ).not.toBeChecked()
    // 依赖关系段保留。
    expect(screen.getByText('依赖关系')).toBeInTheDocument()
  })

  it('patches the draft YAML when a type is toggled', () => {
    const setDefinitionYaml = vi.fn()
    renderSections(startNode, { setDefinitionYaml })

    fireEvent.click(screen.getByRole('checkbox', { name: /整个文件夹/ }))
    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const added = setDefinitionYaml.mock.calls[0][0] as string
    expect(added).toContain('type: start')
    expect(added).toContain('- bundle')

    setDefinitionYaml.mockClear()
    fireEvent.click(screen.getByRole('checkbox', { name: /外部平台内容/ }))
    const removed = setDefinitionYaml.mock.calls[0][0] as string
    expect(removed).toContain('- material')
    expect(removed).not.toContain('- ref')
  })

  it('disables the only checked type so the contract stays non-empty', () => {
    renderSections({ ...startNode, accepted_item_types: ['material'] })

    expect(screen.getByRole('checkbox', { name: /上传文件/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /外部平台内容/ })).toBeEnabled()
    expect(screen.getByRole('checkbox', { name: /整个文件夹/ })).toBeEnabled()
  })
})
