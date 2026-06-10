import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { JobListItem } from './JobListItem'
import type { JobRecord } from '../types'

const mockJob: JobRecord = {
  id: 'j1',
  workspace_id: 'ws1',
  pipeline_key: 'p1',
  source_id: 'Q100',
  title: 'Algebra Problem',
  stem: '',
  status: 'running',
  created_at: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
  completed_nodes: 2,
  total_nodes: 5,
}

describe('JobListItem', () => {
  it('renders source_id, title, and status badge', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={mockJob}
          selected={false}
          expanded={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
          onToggleExpand={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('Algebra Problem - Q100')).toBeInTheDocument()
    expect(screen.getByText('处理中')).toBeInTheDocument()
  })

  it('shows source_id when title is missing', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={{ ...mockJob, title: '' }}
          selected={false}
          expanded={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
          onToggleExpand={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('Q100')).toBeInTheDocument()
  })

  it('checkbox checked state matches prop', () => {
    const { rerender } = render(
      <MemoryRouter>
        <JobListItem
          job={mockJob}
          selected={false}
          expanded={false}
          selectMode={true}
          onToggleSelect={vi.fn()}
          onToggleExpand={vi.fn()}
        />
      </MemoryRouter>
    )

    const checkbox = screen.getByRole('checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(false)

    rerender(
      <MemoryRouter>
        <JobListItem
          job={mockJob}
          selected={true}
          expanded={false}
          selectMode={true}
          onToggleSelect={vi.fn()}
          onToggleExpand={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(checkbox.checked).toBe(true)
  })

  it('clicking expand button calls onToggleExpand', () => {
    const onToggleExpand = vi.fn()
    render(
      <MemoryRouter>
        <JobListItem
          job={mockJob}
          selected={false}
          expanded={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
          onToggleExpand={onToggleExpand}
        />
      </MemoryRouter>
    )

    fireEvent.click(screen.getByText('展开 ▼'))
    expect(onToggleExpand).toHaveBeenCalledTimes(1)
  })

  it('renders collapse text when expanded', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={mockJob}
          selected={false}
          expanded={true}
          selectMode={false}
          onToggleSelect={vi.fn()}
          onToggleExpand={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('收起 ▲')).toBeInTheDocument()
  })

  it('progress bar width is correct', () => {
    const { container } = render(
      <MemoryRouter>
        <JobListItem
          job={mockJob}
          selected={false}
          expanded={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
          onToggleExpand={vi.fn()}
        />
      </MemoryRouter>
    )

    const fill = container.querySelector('[data-progress="40"]')
    expect(fill).toHaveStyle({ width: '40%' })
    expect(screen.getByText('2/5')).toBeInTheDocument()
  })
})
