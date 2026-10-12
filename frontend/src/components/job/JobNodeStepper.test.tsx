import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JobNodeStepper } from './JobNodeStepper'
import styles from './JobNodeStepper.module.css'
import type { JobNodeSummary } from '../../types/jobTypes'

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

  it('keeps the default 4px gap for small node counts', () => {
    render(<JobNodeStepper nodeSummaries={summaries} />)

    expect(screen.getByRole('list', { name: '节点进度' }).style.gap).toBe('4px')
  })

  it('shrinks the track gap at 50+ nodes so every segment stays visible (#1124)', () => {
    const many: JobNodeSummary[] = Array.from({ length: 50 }, (_, i) => ({
      node_key: `node-${i}`,
      label: `节点 ${i}`,
      status: 'pending',
      error_message: '',
    }))
    render(<JobNodeStepper nodeSummaries={many} />)

    expect(screen.getAllByRole('listitem')).toHaveLength(50)
    // 176px track：50 × 2px 段 + 49 × gap ≤ 176，gap 必须收缩到 1px
    // （保持 4px 时 gap 总量 196px 已超宽，段被压成 0 宽）。
    expect(screen.getByRole('list', { name: '节点进度' }).style.gap).toBe('1px')
  })
})

describe('JobNodeStepper 紧凑形态 (#1257)', () => {
  const makeMany = (count: number): JobNodeSummary[] =>
    Array.from({ length: count }, (_, i) => ({
      node_key: `node-${i}`,
      label: `节点 ${i}`,
      status:
        i < 10 ? 'completed' : i === 10 ? 'running' : ('pending' as const),
      error_message: '',
    }))

  it('switches to compact form at 51 nodes and stops rendering per-node segments', () => {
    const { container } = render(
      <JobNodeStepper nodeSummaries={makeMany(51)} activeNodeKey="node-10" />
    )

    expect(screen.queryAllByRole('listitem')).toHaveLength(0)
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
    const progress = screen.getByRole('progressbar', { name: '节点进度' })
    expect(progress).toHaveAttribute('aria-valuemax', '51')
    expect(progress).toHaveAttribute('aria-valuenow', '10')
    expect(progress).toHaveAttribute('aria-valuetext', '11/51 节点 10')
    // DOM 规模与节点数无关：51 与 200 节点的渲染量一致，不会溢出/挤压。
    expect(container.querySelectorAll('div').length).toBeLessThan(10)
  })

  it('shows the current/total count with the current node status at 200 nodes', () => {
    render(
      <JobNodeStepper nodeSummaries={makeMany(200)} activeNodeKey="node-10" />
    )

    const count = screen.getByText('11/200')
    expect(count).toHaveAttribute('data-status', 'running')
    expect(count).toHaveAttribute('data-active', 'true')
    expect(count).toHaveAttribute('title', '节点 10')
  })

  it('resolves the current node without an activeNodeKey (running > failed > pending)', () => {
    const many = makeMany(60)
    many[10].status = 'completed'
    many[20].status = 'failed'
    render(<JobNodeStepper nodeSummaries={many} />)

    expect(screen.getByText('21/60')).toHaveAttribute('data-status', 'failed')
  })

  it('reports the last node and a full bar when all nodes completed', () => {
    const many = makeMany(57).map((s) => ({
      ...s,
      status: 'completed' as const,
    }))
    render(<JobNodeStepper nodeSummaries={many} />)

    expect(screen.getByText('57/57')).toHaveAttribute(
      'data-status',
      'completed'
    )
    const progress = screen.getByRole('progressbar', { name: '节点进度' })
    expect(progress).toHaveAttribute('aria-valuenow', '57')
    expect(progress.firstChild).toHaveStyle({ width: '100%' })
  })

  it('keeps the segmented form at exactly 50 nodes', () => {
    render(<JobNodeStepper nodeSummaries={makeMany(50)} />)

    expect(screen.getAllByRole('listitem')).toHaveLength(50)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('supports a custom compactThreshold', () => {
    render(
      <JobNodeStepper nodeSummaries={makeMany(11)} compactThreshold={10} />
    )

    expect(screen.getByText('11/11')).toBeInTheDocument()
    expect(screen.queryAllByRole('listitem')).toHaveLength(0)
  })

  it('uses the compact form for placeholder segments from totalNodes', () => {
    render(<JobNodeStepper nodeSummaries={[]} totalNodes={120} />)

    const count = screen.getByText('1/120')
    expect(count).toHaveAttribute('data-status', 'pending')
    expect(screen.getByRole('progressbar')).toHaveAttribute(
      'aria-valuenow',
      '0'
    )
  })
})
