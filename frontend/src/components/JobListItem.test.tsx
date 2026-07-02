import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { JobListItem } from './JobListItem'
import type { JobRecord } from '../types'

const mockJob: JobRecord = {
  id: 'j1',
  workspace_id: 'ws1',
  workflow_key: 'question_content',
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
  workflow_revision_id: '',
  workflow_definition_hash: '',
  outcome: '',
  current_workflow_revision_id: '',
  current_workflow_revision_version: null,
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
  it('renders title and description with source type and id', () => {
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

    expect(screen.getByText('Algebra Problem')).toBeInTheDocument()
    expect(screen.getByText(/题目 · Q100/)).toBeInTheDocument()
    expect(screen.getByText('运行中')).toBeInTheDocument()
  })

  it('shows "未命名" title when title is missing', () => {
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

    expect(screen.getByText('未命名')).toBeInTheDocument()
    expect(screen.getByText(/题目 · Q100/)).toBeInTheDocument()
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

    expect(
      screen.getByRole('listitem', { name: '题目理解: completed' })
    ).toHaveAttribute('data-status', 'completed')
    expect(
      screen.getByRole('listitem', { name: '自然语言阅读: running' })
    ).toHaveAttribute('data-status', 'running')
    expect(
      screen.getByRole('listitem', { name: '打包组装: failed' })
    ).toHaveAttribute('data-status', 'failed')
  })

  it('shows active node label on the left of stepper and hides relative time', () => {
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

    const activeLabel = screen.getByText('自然语言阅读')
    expect(activeLabel).toBeInTheDocument()
    expect(activeLabel.tagName.toLowerCase()).toBe('span')
    expect(screen.queryByText(/当前：/)).not.toBeInTheDocument()
    expect(screen.queryByText(/前$/)).not.toBeInTheDocument()
  })

  it('does not render node labels under the stepper', () => {
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

    expect(screen.getByText('自然语言阅读')).toBeInTheDocument()
    expect(screen.queryByText('题目理解')).not.toBeInTheDocument()
    expect(screen.queryByText('打包组装')).not.toBeInTheDocument()
    expect(screen.queryByText('FAQ 生成')).not.toBeInTheDocument()
  })

  it('renders error summary when present', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={{ ...mockJob, error_summary: 'assemble failed' }}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
        />
      </MemoryRouter>
    )
    expect(screen.getByText('assemble failed')).toBeInTheDocument()
    const errorSpan = screen.getByText('assemble failed')
    expect(errorSpan).toHaveAttribute('title', 'assemble failed')
  })

  it('passes totalNodes to JobNodeStepper for empty summaries', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={{ ...mockJob, node_summaries: [], active_node_key: '' }}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
        />
      </MemoryRouter>
    )

    const segments = screen.getAllByRole('listitem')
    expect(segments).toHaveLength(5)
    segments.forEach((segment) => {
      expect(segment).toHaveAttribute('data-status', 'pending')
    })
  })

  it('shows pending placeholder label when summaries are empty but totalNodes is set', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={{
            ...mockJob,
            status: 'pending',
            node_summaries: [],
            active_node_key: '',
          }}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('待调度')).toBeInTheDocument()
  })

  it('shows first pending node label when no active node is set', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={{
            ...mockJob,
            status: 'pending',
            active_node_key: '',
            node_summaries: [
              {
                node_key: 'question_understanding',
                label: '题目理解',
                status: 'completed',
                error_message: '',
              },
              {
                node_key: 'faq_generation',
                label: 'FAQ 生成',
                status: 'pending',
                error_message: '',
              },
            ],
          }}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('FAQ 生成')).toBeInTheDocument()
  })

  it('shows failed node label for failed jobs', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={{
            ...mockJob,
            status: 'failed',
            active_node_key: '',
            node_summaries: [
              {
                node_key: 'question_understanding',
                label: '题目理解',
                status: 'completed',
                error_message: '',
              },
              {
                node_key: 'assemble_package',
                label: '打包组装',
                status: 'failed',
                error_message: 'assemble failed',
              },
            ],
          }}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('打包组装')).toBeInTheDocument()
  })

  it('shows last completed node label for completed jobs', () => {
    render(
      <MemoryRouter>
        <JobListItem
          job={{
            ...mockJob,
            status: 'completed',
            active_node_key: '',
            node_summaries: [
              {
                node_key: 'question_understanding',
                label: '题目理解',
                status: 'completed',
                error_message: '',
              },
              {
                node_key: 'assemble_package',
                label: '打包组装',
                status: 'completed',
                error_message: '',
              },
            ],
          }}
          selected={false}
          selectMode={false}
          onToggleSelect={vi.fn()}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('打包组装')).toBeInTheDocument()
  })
})
