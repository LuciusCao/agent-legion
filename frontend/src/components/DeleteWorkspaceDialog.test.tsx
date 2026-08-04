import { describe, it, expect, vi } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import DeleteWorkspaceDialog from './DeleteWorkspaceDialog'

function createProps(overrides = {}) {
  return {
    open: true,
    workspaceName: 'Test Workspace',
    workspaceId: 'test-workspace',
    onClose: vi.fn(),
    onConfirm: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

describe('DeleteWorkspaceDialog', () => {
  it('renders headline and warning', () => {
    render(<DeleteWorkspaceDialog {...createProps()} />)
    expect(screen.getByText('删除 Workspace')).toBeInTheDocument()
    expect(screen.getByText(/此操作不可撤销/)).toBeInTheDocument()
    expect(screen.getByText(/请输入 Workspace 名称/)).toBeInTheDocument()
  })

  it('does not render when closed', () => {
    const { container } = render(
      <DeleteWorkspaceDialog {...createProps({ open: false })} />
    )
    expect(container.childNodes.length).toBe(0)
  })

  it('disables confirm button until name matches', async () => {
    render(<DeleteWorkspaceDialog {...createProps()} />)
    const confirmBtn = screen.getByText('确认删除')
    const input = screen.getByLabelText('Workspace 名称')

    expect(confirmBtn).toHaveAttribute('disabled')

    await act(async () => {
      ;(input as HTMLInputElement).value = 'Wrong Name'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })
    expect(confirmBtn).toHaveAttribute('disabled')

    await act(async () => {
      ;(input as HTMLInputElement).value = 'Test Workspace'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })
    expect(confirmBtn).not.toHaveAttribute('disabled')
  })

  it('calls onConfirm and onClose on successful delete', async () => {
    const props = createProps()
    render(<DeleteWorkspaceDialog {...props} />)

    const input = screen.getByLabelText('Workspace 名称')
    await act(async () => {
      ;(input as HTMLInputElement).value = 'Test Workspace'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    const confirmBtn = screen.getByText('确认删除')
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(props.onConfirm).toHaveBeenCalledTimes(1)
      expect(props.onClose).toHaveBeenCalledTimes(1)
    })
  })

  it('shows error message when onConfirm throws', async () => {
    const props = createProps({
      onConfirm: vi
        .fn()
        .mockRejectedValue(
          new Error('Cannot delete workspace with running jobs')
        ),
    })
    render(<DeleteWorkspaceDialog {...props} />)

    const input = screen.getByLabelText('Workspace 名称')
    await act(async () => {
      ;(input as HTMLInputElement).value = 'Test Workspace'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    const confirmBtn = screen.getByText('确认删除')
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(
        screen.getByText('Cannot delete workspace with running jobs')
      ).toBeInTheDocument()
    })
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it('disables confirm button when workspace name is empty', async () => {
    render(<DeleteWorkspaceDialog {...createProps({ workspaceName: '' })} />)
    const confirmBtn = screen.getByText('确认删除')
    const input = screen.getByLabelText('Workspace 名称')

    expect(confirmBtn).toHaveAttribute('disabled')

    await act(async () => {
      ;(input as HTMLInputElement).value = ''
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })
    expect(confirmBtn).toHaveAttribute('disabled')
  })

  it('calls onClose when cancel is clicked', () => {
    const props = createProps()
    render(<DeleteWorkspaceDialog {...props} />)

    const cancelBtn = screen.getByText('取消')
    fireEvent.click(cancelBtn)

    expect(props.onClose).toHaveBeenCalledTimes(1)
  })
})
