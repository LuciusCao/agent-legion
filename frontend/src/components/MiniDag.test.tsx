import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MiniDag } from './MiniDag'
import styles from './MiniDag.module.css'

describe('MiniDag', () => {
  const nodes = [
    {
      key: 'download',
      label: '下载',
      status: 'completed' as const,
      duration: 12,
    },
    { key: 'transcribe', label: '转录', status: 'running' as const },
    { key: 'review', label: '审核', status: 'pending' as const },
    { key: 'package', label: '打包', status: 'failed' as const, duration: 5 },
  ]

  it('renders node labels and arrows between nodes', () => {
    render(<MiniDag nodes={nodes} />)

    expect(screen.getByText('下载')).toBeInTheDocument()
    expect(screen.getByText('转录')).toBeInTheDocument()
    expect(screen.getByText('审核')).toBeInTheDocument()
    expect(screen.getByText('打包')).toBeInTheDocument()
    expect(screen.getAllByTestId('mini-dag-arrow')).toHaveLength(3)
  })

  it('renders durations when provided', () => {
    render(<MiniDag nodes={nodes} />)

    expect(screen.getByText('12s')).toBeInTheDocument()
    expect(screen.getByText('5s')).toBeInTheDocument()
  })

  it('applies status color classes', () => {
    const { container } = render(<MiniDag nodes={nodes} />)

    expect(container.querySelector('[data-node="download"]')).toHaveClass(
      styles.completed
    )
    expect(container.querySelector('[data-node="transcribe"]')).toHaveClass(
      styles.running
    )
    expect(container.querySelector('[data-node="review"]')).toHaveClass(
      styles.pending
    )
    expect(container.querySelector('[data-node="package"]')).toHaveClass(
      styles.failed
    )
  })

  it('renders empty track without errors', () => {
    const { container } = render(<MiniDag nodes={[]} />)
    expect(container.querySelectorAll('[data-node]')).toHaveLength(0)
  })
})
