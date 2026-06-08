import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import WorkspaceCard from './WorkspaceCard'

function createProps(overrides = {}) {
  return {
    id: 'ws-1',
    name: 'Test Workspace',
    pipelineLabel: 'Test Pipeline',
    jobStats: { running: 2, completed: 5, failed: 1 },
    agentStatus: { total: 3, busy: 2, idle: 1 },
    onClick: vi.fn(),
    ...overrides,
  }
}

describe('WorkspaceCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders name and pipeline label', () => {
    render(<WorkspaceCard {...createProps()} />)
    expect(screen.getByText('Test Workspace')).toBeInTheDocument()
    expect(screen.getByText('Test Pipeline')).toBeInTheDocument()
  })

  it('renders job stats correctly', () => {
    render(<WorkspaceCard {...createProps()} />)
    const jobsSection = screen.getByText('Jobs').parentElement!
    expect(jobsSection.textContent).toContain('8')
    expect(jobsSection.textContent).toContain('2')
    expect(jobsSection.textContent).toContain('5')
    expect(jobsSection.textContent).toContain('1')
  })

  it('renders agent status correctly', () => {
    render(<WorkspaceCard {...createProps()} />)
    const agentsSection = screen.getByText('Agents').parentElement!
    expect(agentsSection.textContent).toContain('2/3')
  })

  it('calls onClick when clicked', () => {
    const props = createProps()
    render(<WorkspaceCard {...props} />)
    const card = screen.getByRole('button')
    fireEvent.click(card)
    expect(props.onClick).toHaveBeenCalledTimes(1)
  })

  it('calls onClick on Enter key', () => {
    const props = createProps()
    render(<WorkspaceCard {...props} />)
    const card = screen.getByRole('button')
    fireEvent.keyDown(card, { key: 'Enter' })
    expect(props.onClick).toHaveBeenCalledTimes(1)
  })

  it('calls onClick on Space key', () => {
    const props = createProps()
    render(<WorkspaceCard {...props} />)
    const card = screen.getByRole('button')
    fireEvent.keyDown(card, { key: ' ' })
    expect(props.onClick).toHaveBeenCalledTimes(1)
  })

  it('shows delete button for non-system workspaces', () => {
    const onDelete = vi.fn()
    const { container } = render(
      <WorkspaceCard {...createProps({ isSystem: false, onDelete })} />
    )
    const deleteBtn = container.querySelector('md-icon-button')
    expect(deleteBtn).toBeInTheDocument()
  })

  it('hides delete button for system workspaces', () => {
    const onDelete = vi.fn()
    const { container } = render(
      <WorkspaceCard {...createProps({ isSystem: true, onDelete })} />
    )
    const deleteBtn = container.querySelector('md-icon-button')
    expect(deleteBtn).not.toBeInTheDocument()
  })

  it('hides delete button when onDelete is not provided', () => {
    const { container } = render(
      <WorkspaceCard
        {...createProps({ isSystem: false, onDelete: undefined })}
      />
    )
    const deleteBtn = container.querySelector('md-icon-button')
    expect(deleteBtn).not.toBeInTheDocument()
  })

  it('calls onDelete when delete button is clicked', () => {
    const onDelete = vi.fn()
    const { container } = render(
      <WorkspaceCard {...createProps({ isSystem: false, onDelete })} />
    )
    const deleteBtn = container.querySelector('md-icon-button')!
    fireEvent.click(deleteBtn)
    expect(onDelete).toHaveBeenCalledTimes(1)
  })

  it('does not call onClick when delete button is clicked', () => {
    const onClick = vi.fn()
    const onDelete = vi.fn()
    const { container } = render(
      <WorkspaceCard {...createProps({ onClick, isSystem: false, onDelete })} />
    )
    const deleteBtn = container.querySelector('md-icon-button')!
    fireEvent.click(deleteBtn)
    expect(onClick).not.toHaveBeenCalled()
    expect(onDelete).toHaveBeenCalledTimes(1)
  })
})
