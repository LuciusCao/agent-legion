import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import yaml from 'js-yaml'
import { useUiStore } from '../../../stores/uiStore'
import { AddWorkflowNodeDialog } from './AddWorkflowNodeDialog'

const baseYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  intake:',
  '    type: code',
  '    capability: intake',
  '    after: [_start]',
  '',
].join('\n')

function renderDialog(
  props: Partial<React.ComponentProps<typeof AddWorkflowNodeDialog>> = {}
) {
  return render(
    <AddWorkflowNodeDialog
      open
      definitionYaml={baseYaml}
      onClose={() => {}}
      onAppended={() => {}}
      {...props}
    />
  )
}

describe('AddWorkflowNodeDialog', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    const { useUiStore: store } = await import('../../../stores/uiStore')
    store.setState({ toast: null })
  })

  it('appends an approval node without a capability field', () => {
    const onAppended = vi.fn()
    renderDialog({ onAppended })

    fireEvent.change(screen.getByLabelText('节点类型'), {
      target: { value: 'approval' },
    })
    expect(screen.queryByLabelText('能力 Key')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('节点 Key'), {
      target: { value: 'gate' },
    })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    expect(onAppended).toHaveBeenCalledTimes(1)
    const [nextYaml, nodeKey] = onAppended.mock.calls[0]
    expect(nodeKey).toBe('gate')
    const node = (
      yaml.load(nextYaml) as { nodes?: Record<string, Record<string, unknown>> }
    ).nodes?.gate
    expect(node).toEqual({ type: 'approval', label: 'gate', after: [] })
  })

  it('defaults capability and label to the node key for code nodes', () => {
    const onAppended = vi.fn()
    renderDialog({ onAppended })

    fireEvent.change(screen.getByLabelText('节点 Key'), {
      target: { value: 'draft' },
    })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    const node = (
      yaml.load(onAppended.mock.calls[0][0]) as {
        nodes?: Record<string, Record<string, unknown>>
      }
    ).nodes?.draft
    expect(node).toEqual({
      type: 'code',
      label: 'draft',
      capability: 'draft',
      after: [],
    })
  })

  it('toasts the reason and keeps the draft untouched on duplicate key', async () => {
    const onAppended = vi.fn()
    renderDialog({ onAppended })

    fireEvent.change(screen.getByLabelText('节点 Key'), {
      target: { value: 'intake' },
    })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    expect(onAppended).not.toHaveBeenCalled()
    await vi.waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('已存在')
    )
  })

  it('renders nothing when closed', () => {
    const { container } = renderDialog({ open: false })
    expect(container).toBeEmptyDOMElement()
  })
})
