import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { WorkspaceStatCards } from './WorkspaceStatCards'
import styles from './WorkspaceStatCards.module.css'

describe('WorkspaceStatCards', () => {
  const onFilterChange = vi.fn()

  beforeEach(() => {
    onFilterChange.mockReset()
  })

  it('renders five filter pills with counts', () => {
    render(
      <WorkspaceStatCards
        counts={{ all: 12, pending: 3, running: 4, completed: 4, failed: 1 }}
        activeFilter="all"
        onFilterChange={onFilterChange}
      />
    )

    expect(screen.getByText('全部（12）')).toBeInTheDocument()
    expect(screen.getByText('等待中（3）')).toBeInTheDocument()
    expect(screen.getByText('运行中（4）')).toBeInTheDocument()
    expect(screen.getByText('已完成（4）')).toBeInTheDocument()
    expect(screen.getByText('失败（1）')).toBeInTheDocument()
  })

  it('uses zero when count is missing', () => {
    render(
      <WorkspaceStatCards
        counts={{}}
        activeFilter="all"
        onFilterChange={onFilterChange}
      />
    )

    expect(screen.getByText('全部（0）')).toBeInTheDocument()
  })

  it('highlights the active filter with status color classes', () => {
    const { container } = render(
      <WorkspaceStatCards
        counts={{ all: 5, running: 2 }}
        activeFilter="running"
        onFilterChange={onFilterChange}
      />
    )

    const active = container.querySelector('[data-filter="running"]')
    expect(active).toHaveClass(styles.active)
    expect(active).toHaveClass(styles.running)
  })

  it('does not highlight inactive filters', () => {
    const { container } = render(
      <WorkspaceStatCards
        counts={{ all: 5, pending: 1 }}
        activeFilter="all"
        onFilterChange={onFilterChange}
      />
    )

    const pending = container.querySelector('[data-filter="pending"]')
    expect(pending).not.toHaveClass(styles.active)
  })

  it('calls onFilterChange when a pill is clicked', () => {
    render(
      <WorkspaceStatCards
        counts={{ all: 12, failed: 1 }}
        activeFilter="all"
        onFilterChange={onFilterChange}
      />
    )

    fireEvent.click(screen.getByText('失败（1）'))
    expect(onFilterChange).toHaveBeenCalledTimes(1)
    expect(onFilterChange).toHaveBeenCalledWith('failed')
  })
})
