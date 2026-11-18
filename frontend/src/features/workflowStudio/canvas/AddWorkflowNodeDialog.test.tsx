import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

  it('defaults code capability to empty and only label to the node key', () => {
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
      capability: '',
      after: [],
    })
  })

  it('preserves an explicit non-empty code capability', () => {
    const onAppended = vi.fn()
    renderDialog({ onAppended })

    fireEvent.change(screen.getByLabelText('节点 Key'), {
      target: { value: 'legacy' },
    })
    fireEvent.change(screen.getByLabelText('能力 Key'), {
      target: { value: 'legacy_capability' },
    })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    const node = (
      yaml.load(onAppended.mock.calls[0][0]) as {
        nodes?: Record<string, Record<string, unknown>>
      }
    ).nodes?.legacy
    expect(node?.capability).toBe('legacy_capability')
  })

  it('appends an agent with an empty optional capability and display label', () => {
    const onAppended = vi.fn()
    renderDialog({ onAppended })

    fireEvent.change(screen.getByLabelText('节点类型'), {
      target: { value: 'agent' },
    })
    expect(
      screen.getByText('能力 Key（code / agent 均为可选标签）')
    ).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('节点 Key'), {
      target: { value: 'draft_agent' },
    })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    const node = (
      yaml.load(onAppended.mock.calls[0][0]) as {
        nodes?: Record<string, Record<string, unknown>>
      }
    ).nodes?.draft_agent
    expect(node?.capability).toBe('')
    expect(node?.label).toBe('draft_agent')
    expect(node?.execution).toEqual({ runtime: 'velites' })
  })

  it('submits a capless code node from the keyboard', async () => {
    const user = userEvent.setup()
    const onAppended = vi.fn()
    renderDialog({ onAppended })

    await user.type(screen.getByLabelText('节点 Key'), 'keyboard')
    const addButton = screen.getByRole('button', { name: '添加' })
    await user.tab()
    await user.tab()
    await user.tab()
    await user.tab()
    expect(addButton).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(onAppended).toHaveBeenCalledTimes(1)
    const node = (
      yaml.load(onAppended.mock.calls[0][0]) as {
        nodes?: Record<string, Record<string, unknown>>
      }
    ).nodes?.keyboard
    expect(node?.capability).toBe('')
  })

  it('keeps the small full-width dialog layout used on narrow screens', () => {
    renderDialog()

    expect(screen.getByRole('dialog')).toHaveClass(
      'MuiDialog-paperFullWidth',
      'MuiDialog-paperWidthSm'
    )
    expect(screen.getByLabelText('能力 Key')).toBeVisible()
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
