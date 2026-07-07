import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import WorkspaceCard from './WorkspaceCard'

function createProps(overrides = {}) {
  return {
    id: 'ws-1',
    name: 'Test Workspace',
    workflowLabel: 'Test Workflow',
    jobStats: { running: 2, completed: 5, failed: 1 },
    executorStatus: [
      {
        executor_id: 'local-default',
        kind: 'local',
        global_capacity: 16,
        workspace_limit: 4,
        running: 2,
        available: 1,
        binding_count: 1,
      },
    ],
    onClick: vi.fn(),
    ...overrides,
  }
}

describe('WorkspaceCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders name and workflow label', () => {
    render(<WorkspaceCard {...createProps()} />)
    expect(screen.getByText('Test Workspace')).toBeInTheDocument()
    expect(screen.getByText('Test Workflow')).toBeInTheDocument()
  })

  it('renders job stats correctly', () => {
    render(<WorkspaceCard {...createProps()} />)
    const jobsSection = screen.getByText('任务').parentElement!
    expect(jobsSection.textContent).toContain('8')
    expect(jobsSection.textContent).toContain('2')
    expect(jobsSection.textContent).toContain('5')
    expect(jobsSection.textContent).toContain('1')
  })

  it('renders executor status correctly', () => {
    render(<WorkspaceCard {...createProps()} />)
    const executorsSection = screen.getByText('执行器').parentElement!
    expect(executorsSection.textContent).toContain('2/1')
  })

  it('calls onClick when clicked', () => {
    const props = createProps()
    render(<WorkspaceCard {...props} />)
    const card = screen.getByTestId('workspace-card')
    fireEvent.click(card)
    expect(props.onClick).toHaveBeenCalledTimes(1)
  })

  it('calls onClick on Enter key', () => {
    const props = createProps()
    render(<WorkspaceCard {...props} />)
    const card = screen.getByTestId('workspace-card')
    fireEvent.keyDown(card, { key: 'Enter' })
    expect(props.onClick).toHaveBeenCalledTimes(1)
  })

  it('calls onClick on Space key', () => {
    const props = createProps()
    render(<WorkspaceCard {...props} />)
    const card = screen.getByTestId('workspace-card')
    fireEvent.keyDown(card, { key: ' ' })
    expect(props.onClick).toHaveBeenCalledTimes(1)
  })
})
