import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { JobProgressPanel } from './JobProgressPanel'
import type { JobNode } from '../../types/jobTypes'

vi.mock('../../api/jobApi', () => ({
  fetchJobLog: vi.fn(),
  fetchRunTokenUsage: vi.fn(() => new Promise(() => {})),
}))

function node(overrides: Partial<JobNode>): JobNode {
  return {
    id: 1,
    job_id: 'j1',
    node_key: 'extract',
    label: '提取',
    status: 'completed',
    after: [],
    created_at: '2026-06-09T07:59:00Z',
    error_message: '',
    stale_reason: '',
    capability: 'extract',
    inputs: [],
    outputs: [],
    ...overrides,
  }
}

function renderPanel(nodes: JobNode[]) {
  render(
    <JobProgressPanel
      jobId="j1"
      nodes={nodes}
      runs={[]}
      onOpenDagDialog={vi.fn()}
    />,
    { wrapper: TestQueryProvider }
  )
}

describe('JobNodeHydrationDefer (#887)', () => {
  it('shows the stuck reason and the producer to rerun on the waiting node', () => {
    renderPanel([
      node({}),
      node({
        id: 2,
        node_key: 'generate',
        label: '生成',
        status: 'pending',
        after: ['extract'],
        inputs: ['extract.json'],
        hydration_defer: {
          inputs: ['extract.json'],
          reasons: ['object_missing'],
          rerun_nodes: ['extract'],
        },
      }),
    ])

    const hint = screen.getByRole('status')
    expect(hint).toHaveTextContent('输入恢复不全，建议重跑 提取')
    expect(hint).toHaveAttribute('title', '输入 extract.json：对象已缺失')
  })

  it('renders nothing extra for ordinary queued nodes', () => {
    renderPanel([
      node({}),
      node({ id: 2, node_key: 'generate', label: '生成', status: 'pending' }),
    ])

    expect(screen.queryByText(/输入恢复不全/)).not.toBeInTheDocument()
  })
})
