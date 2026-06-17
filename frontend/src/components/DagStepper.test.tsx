import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DagStepper } from './DagStepper'
import type { JobNodeRecord } from '../types'

describe('DagStepper', () => {
  const makeNodes = (count: number) =>
    Array.from({ length: count }, (_, i) => ({
      id: i,
      job_id: 'job-1',
      node_key: `node-${i}`,
      label: `节点 ${i}`,
      status: i === 0 ? 'completed' : i === 1 ? 'running' : 'pending',
      after: i > 0 ? [`node-${i - 1}`] : [],
    })) as JobNodeRecord[]

  it('does not render node labels', () => {
    render(<DagStepper nodes={makeNodes(8)} />)
    expect(screen.queryByText('节点 0')).not.toBeInTheDocument()
    expect(screen.queryByText('节点 7')).not.toBeInTheDocument()
  })

  it('applies status classes', () => {
    const { container } = render(<DagStepper nodes={makeNodes(3)} />)
    const bars = container.querySelectorAll('[class*="stepBar"]')
    expect(bars[0].className).toContain('completed')
    expect(bars[1].className).toContain('running')
    expect(bars[2].className).toContain('pending')
  })
})
