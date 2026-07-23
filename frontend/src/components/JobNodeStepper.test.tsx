import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JobNodeStepper } from './JobNodeStepper'
import styles from './JobNodeStepper.module.css'
import type { JobNodeSummary } from '../types/jobTypes'

const summaries: JobNodeSummary[] = [
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
  {
    node_key: 'content_review',
    label: '内容审核',
    status: 'stale',
    error_message: '',
  },
]

describe('JobNodeStepper', () => {
  it('renders one segment per persisted node with data-status', () => {
    render(<JobNodeStepper nodeSummaries={summaries} />)

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
    expect(screen.getByTitle('FAQ 生成')).toHaveAttribute(
      'data-status',
      'pending'
    )
    expect(screen.getByTitle('内容审核')).toHaveAttribute(
      'data-status',
      'stale'
    )
  })

  it('marks the inner bar of a stale segment with data-status="stale"', () => {
    render(<JobNodeStepper nodeSummaries={summaries} />)

    const staleSegment = screen.getByTitle('内容审核')
    const bar = staleSegment.querySelector(`.${styles.bar}`)
    expect(bar).toHaveAttribute('data-status', 'stale')
  })

  it('sets accessible aria-label with node label and status', () => {
    render(<JobNodeStepper nodeSummaries={summaries} />)

    expect(screen.getByTitle('题目理解')).toHaveAttribute(
      'aria-label',
      '题目理解: completed'
    )
  })

  it('does not render node labels', () => {
    render(<JobNodeStepper nodeSummaries={summaries} />)

    expect(screen.queryByText('题目理解')).not.toBeInTheDocument()
    expect(screen.queryByText('自然语言阅读')).not.toBeInTheDocument()
    expect(screen.queryByText('打包组装')).not.toBeInTheDocument()
  })

  it('sets data-active on the segment matching activeNodeKey only', () => {
    render(
      <JobNodeStepper
        nodeSummaries={summaries}
        activeNodeKey="natural_language_reading"
      />
    )

    const activeSegment = screen.getByTitle('自然语言阅读')
    expect(activeSegment).toHaveAttribute('data-active', 'true')

    const inactiveLabels = ['题目理解', '打包组装', 'FAQ 生成', '内容审核']
    inactiveLabels.forEach((label) => {
      expect(screen.getByTitle(label)).not.toHaveAttribute('data-active')
    })
  })

  it('renders placeholder when no summaries and no totalNodes are provided', () => {
    render(<JobNodeStepper nodeSummaries={[]} />)

    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('renders pending segments from totalNodes when summaries are empty', () => {
    render(<JobNodeStepper nodeSummaries={[]} totalNodes={4} />)

    const segments = screen.getAllByRole('listitem')
    expect(segments).toHaveLength(4)
    segments.forEach((segment) => {
      expect(segment).toHaveAttribute('data-status', 'pending')
    })
  })
})
