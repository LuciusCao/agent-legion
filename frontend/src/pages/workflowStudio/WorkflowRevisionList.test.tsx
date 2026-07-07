import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowRevisionList } from './WorkflowRevisionList'
import type { WorkflowRevisionSummary } from '../../types'

const revisions: WorkflowRevisionSummary[] = [
  {
    id: 'rev-active',
    workspace_id: 'ws1',
    workflow_key: 'wf',
    version: 2,
    status: 'active',
    definition_hash: 'abcdef123456',
    created_at: '2026-07-06T10:00:00Z',
    published_at: '2026-07-06T10:05:00Z',
  },
  {
    id: 'rev-old',
    workspace_id: 'ws1',
    workflow_key: 'wf',
    version: 1,
    status: 'archived',
    definition_hash: '123456abcdef',
    created_at: '2026-07-05T10:00:00Z',
    published_at: '2026-07-05T10:05:00Z',
  },
]

describe('WorkflowRevisionList', () => {
  it('renders revisions as selectable buttons', () => {
    const onSelectRevision = vi.fn()
    render(
      <WorkflowRevisionList
        revisions={revisions}
        activeRevisionId="rev-active"
        selectedRevisionId="rev-old"
        onSelectRevision={onSelectRevision}
      />
    )

    const oldRevision = screen.getByRole('button', { name: /v1/ })
    expect(oldRevision).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByRole('button', { name: /v2/ }))

    expect(onSelectRevision).toHaveBeenCalledWith('rev-active')
  })

  it('disables revision items while loading', () => {
    render(
      <WorkflowRevisionList
        revisions={revisions}
        activeRevisionId="rev-active"
        selectedRevisionId="rev-old"
        isLoadingRevision
        onSelectRevision={vi.fn()}
      />
    )

    expect(screen.getByRole('button', { name: /v1/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /v2/ })).toBeDisabled()
  })

  it('shows an inline error when revision loading fails', () => {
    render(
      <WorkflowRevisionList
        revisions={revisions}
        activeRevisionId="rev-active"
        selectedRevisionId="rev-old"
        revisionLoadError="network error"
        onSelectRevision={vi.fn()}
      />
    )

    expect(screen.getByText(/加载版本失败/)).toHaveTextContent(
      '加载版本失败：network error'
    )
  })
})
