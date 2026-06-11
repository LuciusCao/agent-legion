import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DagFullscreenDialog } from './DagFullscreenDialog'

const nodes = [
  { key: 'a', label: '提取', status: 'completed' as const },
  { key: 'b', label: '生成', status: 'running' as const },
]
const edges = [{ from: 'a', to: 'b' }]

describe('DagFullscreenDialog', () => {
  it('renders when open', () => {
    render(
      <DagFullscreenDialog
        open={true}
        nodes={nodes}
        edges={edges}
        onClose={vi.fn()}
      />
    )
    expect(screen.getByText('提取')).toBeInTheDocument()
    expect(screen.getByText('生成')).toBeInTheDocument()
    expect(screen.getByLabelText('关闭')).toBeInTheDocument()
  })

  it('does not render when closed', () => {
    const { container } = render(
      <DagFullscreenDialog
        open={false}
        nodes={nodes}
        edges={edges}
        onClose={vi.fn()}
      />
    )
    expect(container.firstChild).toBeNull()
  })

  it('calls onClose when close button clicked', () => {
    const onClose = vi.fn()
    render(
      <DagFullscreenDialog
        open={true}
        nodes={nodes}
        edges={edges}
        onClose={onClose}
      />
    )
    fireEvent.click(screen.getByLabelText('关闭'))
    expect(onClose).toHaveBeenCalled()
  })
})
