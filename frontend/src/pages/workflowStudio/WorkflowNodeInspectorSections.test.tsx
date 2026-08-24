import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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

function renderSections(node: WorkflowNodeRecord) {
  return render(
    <WorkflowNodeInspectorSections
      details={{ node, incoming: [], outgoing: [] }}
      agentCatalog={[]}
      definitionYaml=""
      setDefinitionYaml={() => {}}
      workflowKey="demo_workflow"
    />
  )
}

describe('WorkflowNodeInspectorSections for a start node', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useSettingStore.setState({ workspaceId: 'default' })
  })

  it('shows the read-only entry contract instead of code/execution editors', () => {
    renderSections(startNode)

    expect(screen.getByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.getByText(/material、ref/)).toBeInTheDocument()
    // The capability/execution/code editors do not apply to a node that
    // never executes (the backend 404s its node-code endpoints).
    expect(screen.queryByLabelText('节点代码')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('节点执行能力')).not.toBeInTheDocument()
    // No node-code fetch for a start node.
    expect(mockApi).not.toHaveBeenCalled()
    // Structure info stays available.
    expect(screen.getByText('依赖关系')).toBeInTheDocument()
  })
})
