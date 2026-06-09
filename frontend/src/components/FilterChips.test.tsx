import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FilterChips } from './FilterChips'
import styles from './FilterChips.module.css'

describe('FilterChips', () => {
  const filters = [
    { key: 'all', label: '全部', count: 12 },
    { key: 'mine', label: '我的', count: 3 },
    { key: 'system', label: '系统', count: 9 },
  ]
  const onChange = vi.fn()

  beforeEach(() => {
    onChange.mockReset()
  })

  it('renders chips with labels and counts', () => {
    render(<FilterChips filters={filters} activeKey="all" onChange={onChange} />)

    expect(screen.getByText('全部')).toBeInTheDocument()
    expect(screen.getByText('我的')).toBeInTheDocument()
    expect(screen.getByText('系统')).toBeInTheDocument()
    expect(screen.getByText('（12）')).toBeInTheDocument()
    expect(screen.getByText('（3）')).toBeInTheDocument()
    expect(screen.getByText('（9）')).toBeInTheDocument()
  })

  it('highlights active chip', () => {
    const { container } = render(
      <FilterChips filters={filters} activeKey="mine" onChange={onChange} />
    )

    const active = container.querySelector('[data-chip="mine"]')
    expect(active).toHaveClass(styles.active)
  })

  it('does not highlight inactive chips', () => {
    const { container } = render(
      <FilterChips filters={filters} activeKey="mine" onChange={onChange} />
    )

    expect(container.querySelector('[data-chip="all"]')).not.toHaveClass(
      styles.active
    )
    expect(container.querySelector('[data-chip="system"]')).not.toHaveClass(
      styles.active
    )
  })

  it('calls onChange when a chip is clicked', () => {
    render(<FilterChips filters={filters} activeKey="all" onChange={onChange} />)

    fireEvent.click(screen.getByText('系统'))
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith('system')
  })

  it('renders empty list without errors', () => {
    const { container } = render(
      <FilterChips filters={[]} activeKey="all" onChange={onChange} />
    )

    expect(container.querySelectorAll('[data-chip]')).toHaveLength(0)
  })
})
