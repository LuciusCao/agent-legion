import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { components } from '../../../generated/api'
import { WorkflowPublishReviewDialog } from './WorkflowPublishReviewDialog'
import { buildChangeSummary } from './workflowStudioChanges'

type CompareResponse = components['schemas']['WorkflowDraftCompareResponse']

const summary = buildChangeSummary({
  valid: true,
  creates_revision: false,
  base_revision: null,
  draft_workflow: null,
  summary: {
    risk_level: 'none',
    node_changes: [],
    edge_changes: [],
    intake_changes: [],
    metadata_changes: [],
    risk_flags: [],
  },
  errors: [],
} satisfies CompareResponse)

function renderDialog(advisories: string[], onConfirm = vi.fn()) {
  render(
    <WorkflowPublishReviewDialog
      open
      activeRevision={null}
      nextVersion={2}
      createsRevision={false}
      definitionHash={null}
      summary={summary}
      runtimeChangedKeys={['nodes.generate.model']}
      advisories={advisories}
      onConfirm={onConfirm}
      onCancel={vi.fn()}
    />
  )
  return onConfirm
}

describe('WorkflowPublishReviewDialogAdvisories', () => {
  it('keeps the warning typography and newline presentation after extraction', () => {
    renderDialog(['first advisory', 'second advisory'])

    const notice = screen.getByTestId('publish-advisories')
    expect(notice.tagName).toBe('P')
    expect(notice).toHaveClass('MuiTypography-body2')
    expect(notice).toHaveStyle({ whiteSpace: 'pre-line' })
    expect(notice.textContent).toBe('first advisory\nsecond advisory')
  })

  it('renders no empty notice and never blocks confirmation', async () => {
    const { rerender } = render(
      <WorkflowPublishReviewDialog
        open
        activeRevision={null}
        nextVersion={2}
        createsRevision={false}
        definitionHash={null}
        summary={summary}
        runtimeChangedKeys={['nodes.generate.model']}
        advisories={[]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )
    expect(screen.queryByTestId('publish-advisories')).not.toBeInTheDocument()

    rerender(
      <WorkflowPublishReviewDialog
        open
        activeRevision={null}
        nextVersion={2}
        createsRevision={false}
        definitionHash={null}
        summary={summary}
        runtimeChangedKeys={['nodes.generate.model']}
        advisories={['legacy skill still declares a contract']}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    )
    expect(screen.getByTestId('publish-advisories')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认保存' })).toBeEnabled()
  })
})
