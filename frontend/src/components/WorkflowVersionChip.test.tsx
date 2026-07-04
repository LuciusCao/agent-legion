import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { WorkflowVersionChip } from './WorkflowVersionChip'

describe('WorkflowVersionChip', () => {
  it('renders current version when up to date', () => {
    render(
      <WorkflowVersionChip
        job={{
          workflow_version: 3,
          current_workflow_revision_version: 3,
          is_workflow_outdated: false,
        }}
      />
    )

    expect(screen.getByText('v3')).toBeInTheDocument()
    expect(screen.queryByText('v5')).not.toBeInTheDocument()
  })

  it('renders upgrade arrow and latest version when outdated', () => {
    render(
      <WorkflowVersionChip
        job={{
          workflow_version: 1,
          current_workflow_revision_version: 2,
          is_workflow_outdated: true,
        }}
      />
    )

    expect(screen.getByText('v1')).toBeInTheDocument()
    expect(screen.getByText('v2')).toBeInTheDocument()
  })

  it('returns null when workflow version is missing', () => {
    const { container } = render(
      <WorkflowVersionChip
        job={{
          workflow_version: null,
          current_workflow_revision_version: null,
          is_workflow_outdated: false,
        }}
      />
    )

    expect(container.firstChild).toBeNull()
  })
})
