import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { JobListItem } from './JobListItem'
import type { JobRecord } from '../types'

const mockJob: JobRecord = {
  id: 'j1',
  workspace_id: 'ws1',
  pipeline_key: 'question_content',
  source_id: 'Q100',
  source_type: 'question',
  title: 'Algebra Problem',
  status: 'running',
  batch_id: 'b1',
  created_at: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
  updated_at: new Date().toISOString(),
  storage_dir: '/tmp/j1',
  error_message: '',
  error_summary: '',
  completed_nodes: 2,
  total_nodes: 5,
  active_node_key: 'natural_language_reading',
  node_summaries: [
    {
      node_key: 'question_understanding',
      label: '题目理解',
      status: 'completed',
      error_message: '',
    },
    {
      node_key: 'natural_language_reading',
      label: '自然语言阅读',
      status: 'running',
      error_message: '',
    },
    {
      node_key: 'assemble_package',
      label: '打包组装',
      status: 'failed',
      error_message: 'assemble failed',
    },
    {
      node_key: 'faq_generation',
      label: 'FAQ 生成',
      status: 'pending',
      error_message: '',
    },
  ],
}

describe('JobListItem', () => {
  it('renders source_id, title, and status badge', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={mockJob}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
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
          selectMode={false}
          onToggleSelect={vi.fn()}
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
          selectMode={true}
          onToggleSelect={vi.fn()}
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
          selectMode={true}
          onToggleSelect={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(checkbox.checked).toBe(true)
  })

  it('renders persisted node stepper with statuses', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={mockJob}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(screen.getByTitle('题目理解')).toHaveAttribute(
      'data-status',
      'completed'
    )
    expect(screen.getByTitle('自然语言阅读')).toHaveAttribute(
      'data-status',
      'running'
    )
    expect(screen.getByTitle('打包组装')).toHaveAttribute(
      'data-status',
      'failed'
    )
  })

  it('shows completed/total count, active node label, and error summary', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={{
            ...mockJob,
            error_summary: 'assemble failed',
          }}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('2/5')).toBeInTheDocument()
    expect(screen.getByText('当前：自然语言阅读')).toBeInTheDocument()
    expect(screen.getAllByText('assemble failed')).toHaveLength(2)
  })
})
