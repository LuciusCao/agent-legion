import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { BatchDeleteDialog } from './BatchDeleteDialog'

describe('BatchDeleteDialog', () => {
  it('shows the selected count', () => {
    render(
      <BatchDeleteDialog
        open
        count={3}
        onClose={vi.fn()}
        onConfirm={vi.fn().mockResolvedValue(undefined)}
      />
    )
    expect(screen.getByText(/3/)).toBeInTheDocument()
  })

  it('calls onConfirm when the delete button is clicked', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()

    render(
      <BatchDeleteDialog
        open
        count={2}
        onClose={onClose}
        onConfirm={onConfirm}
      />
    )

    await act(async () => {
      fireEvent.click(screen.getByText('删除'))
    })

    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('calls onClose when the cancel button is clicked', () => {
    const onClose = vi.fn()
    const onConfirm = vi.fn().mockResolvedValue(undefined)

    render(
      <BatchDeleteDialog
        open
        count={1}
        onClose={onClose}
        onConfirm={onConfirm}
      />
    )

    fireEvent.click(screen.getByText('取消'))

    expect(onClose).toHaveBeenCalledOnce()
  })

  it('renders nothing when not open', () => {
    const { container } = render(
      <BatchDeleteDialog
        open={false}
        count={0}
        onClose={vi.fn()}
        onConfirm={vi.fn().mockResolvedValue(undefined)}
      />
    )
    expect(container.firstChild).toBeNull()
  })
})
