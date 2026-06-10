import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DagStepper } from './DagStepper'

describe('DagStepper', () => {
  const makeNodes = (count: number) =>
    Array.from({ length: count }, (_, i) => ({
      id: i,
      job_id: 'job-1',
      node_key: `node-${i}`,
      label: `节点 ${i}`,
      status: i === 0 ? 'completed' : i === 1 ? 'running' : 'pending',
      after: i > 0 ? [`node-${i - 1}`] : [],
    }))

  it('shows labels when node count <= 8', () => {
    render(<DagStepper nodes={makeNodes(8)} />)
    expect(screen.getByText('节点 0')).toBeInTheDocument()
    expect(screen.getByText('节点 7')).toBeInTheDocument()
  })

  it('hides labels when node count > 8', () => {
    const { container } = render(<DagStepper nodes={makeNodes(9)} />)
    expect(container.firstChild?.className).toContain('compact')
  })

  it('applies status classes', () => {
    const { container } = render(<DagStepper nodes={makeNodes(3)} />)
    const bars = container.querySelectorAll('[class*="stepBar"]')
    expect(bars[0].className).toContain('completed')
    expect(bars[1].className).toContain('running')
    expect(bars[2].className).toContain('pending')
  })
})
