import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AgentPills } from './AgentPills'
import styles from './AgentPills.module.css'

describe('AgentPills', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders idle and busy agent pills', () => {
    render(
      <AgentPills
        agents={[
          { id: 'agent-1', status: 'idle' },
          { id: 'agent-2', status: 'busy', currentJobId: 'Q12345' },
        ]}
        maxConcurrency={4}
      />
    )

    expect(screen.getByText('agent-1')).toBeInTheDocument()
    expect(screen.getByText(/agent-2/)).toBeInTheDocument()
    expect(screen.getByText(/Q12345/)).toBeInTheDocument()
    expect(screen.getByText(/1\/2 可用/)).toBeInTheDocument()
    expect(screen.getByText(/并发上限 4/)).toBeInTheDocument()
  })

  it('renders empty state without errors', () => {
    render(<AgentPills agents={[]} maxConcurrency={2} />)

    expect(screen.getByText(/0\/0 可用/)).toBeInTheDocument()
    expect(screen.getByText(/并发上限 2/)).toBeInTheDocument()
  })

  it('applies idle and busy visual classes', () => {
    const { container } = render(
      <AgentPills
        agents={[
          { id: 'a1', status: 'idle' },
          { id: 'a2', status: 'busy', currentJobId: 'J99' },
        ]}
        maxConcurrency={2}
      />
    )

    expect(container.querySelector('[data-agent="a1"]')).toHaveClass(
      styles.idle
    )
    expect(container.querySelector('[data-agent="a2"]')).toHaveClass(
      styles.busy
    )
  })

  it('shows current job id only for busy agents', () => {
    render(
      <AgentPills
        agents={[
          { id: 'a1', status: 'idle' },
          { id: 'a2', status: 'busy', currentJobId: 'JOB-7' },
        ]}
        maxConcurrency={2}
      />
    )

    expect(screen.queryByText(/JOB-7/)).toBeInTheDocument()
    const idlePill = screen.getByText('a1').closest('[data-agent]')
    expect(idlePill?.textContent).not.toContain('·')
  })
})
