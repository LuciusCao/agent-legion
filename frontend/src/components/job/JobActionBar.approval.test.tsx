import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { JobActionBar } from './JobActionBar'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { makeJob } from '../../testing/fixtures'

function renderApprovalBar({
  onApproval = vi.fn(),
  allMatchingCount = null as number | null,
  jobs = [
    makeJob({ id: 'j1', status: 'awaiting_approval' }),
    makeJob({
      id: 'j2',
      status: 'running',
      node_summaries: [
        {
          node_key: 'gate',
          label: '审批门',
          status: 'awaiting_approval',
          error_message: '',
        },
      ],
    }),
    makeJob({ id: 'j3', status: 'completed' }),
  ],
  selectedCount = 3,
} = {}) {
  render(
    <JobActionBar
      jobs={jobs}
      mode="batch"
      selectedCount={selectedCount}
      allMatchingCount={allMatchingCount}
      onApproval={onApproval}
      onRerun={vi.fn()}
      onPackage={vi.fn()}
      onDelete={vi.fn()}
    />,
    { wrapper: TestQueryProvider }
  )
  return { onApproval }
}

describe('JobActionBar batch approvals', () => {
  it('shows selected, executable and skipped counts before approval', async () => {
    const user = userEvent.setup()
    const { onApproval } = renderApprovalBar()

    await user.click(screen.getByRole('button', { name: '通过' }))
    expect(screen.getByText('确认批量通过')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toHaveTextContent(
      '已选择 3 项，预计执行 2 项、跳过 1 项'
    )

    await user.click(screen.getByRole('button', { name: '确认通过' }))
    await waitFor(() => expect(onApproval).toHaveBeenCalledWith('approve'))
    expect(screen.queryByText('确认批量通过')).not.toBeInTheDocument()
  })

  it('confirms termination through the same submission channel', async () => {
    const user = userEvent.setup()
    const { onApproval } = renderApprovalBar()

    await user.click(screen.getByRole('button', { name: '终止' }))
    expect(screen.getByText('确认批量终止')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认终止' }))

    await waitFor(() => expect(onApproval).toHaveBeenCalledWith('reject'))
  })

  it('disables both decisions when no selected job awaits approval', () => {
    renderApprovalBar({
      jobs: [makeJob({ status: 'completed' })],
      selectedCount: 1,
    })
    expect(screen.getByRole('button', { name: '通过' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '终止' })).toBeDisabled()
  })

  it('disables decisions for an all-matching selection with unknown counts', () => {
    renderApprovalBar({ allMatchingCount: 20, selectedCount: 20 })
    expect(screen.getByRole('button', { name: '通过' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '终止' })).toBeDisabled()
  })
})
