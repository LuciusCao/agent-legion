import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { BatchUpgradeDialog } from './BatchUpgradeDialog'

describe('BatchUpgradeDialog', () => {
  it('shows selected, upgradeable, and completed counts', () => {
    render(
      <BatchUpgradeDialog
        open
        jobs={[
          {
            id: 'j1',
            name: 'Job 1',
            status: 'completed',
            isWorkflowOutdated: true,
          },
          {
            id: 'j2',
            name: 'Job 2',
            status: 'pending',
            isWorkflowOutdated: true,
          },
          {
            id: 'j3',
            name: 'Job 3',
            status: 'running',
            isWorkflowOutdated: true,
          },
          {
            id: 'j4',
            name: 'Job 4',
            status: 'completed',
            isWorkflowOutdated: false,
          },
        ]}
        onClose={vi.fn()}
        onConfirm={vi.fn().mockResolvedValue(undefined)}
      />
    )
    expect(screen.getByText(/已选择 4 个任务/)).toBeInTheDocument()
    expect(screen.getByText(/可升级 2 个/)).toBeInTheDocument()
    expect(screen.getByText(/其中 2 个已完成/)).toBeInTheDocument()
  })

  it('disables confirm when nothing is upgradeable', () => {
    render(
      <BatchUpgradeDialog
        open
        jobs={[
          {
            id: 'j1',
            name: 'Job 1',
            status: 'running',
            isWorkflowOutdated: true,
          },
        ]}
        onClose={vi.fn()}
        onConfirm={vi.fn().mockResolvedValue(undefined)}
      />
    )
    expect(screen.getByText('升级 0 个任务')).toHaveAttribute('disabled')
  })

  it('calls onConfirm with only upgradeable job ids', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    render(
      <BatchUpgradeDialog
        open
        jobs={[
          {
            id: 'j1',
            name: 'Job 1',
            status: 'completed',
            isWorkflowOutdated: true,
          },
          {
            id: 'j2',
            name: 'Job 2',
            status: 'running',
            isWorkflowOutdated: true,
          },
          {
            id: 'j3',
            name: 'Job 3',
            status: 'completed',
            isWorkflowOutdated: false,
          },
        ]}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />
    )
    await act(async () => {
      fireEvent.click(screen.getByText('升级 1 个任务'))
    })
    expect(onConfirm).toHaveBeenCalledWith(['j1'])
  })

  it('shows skip reasons for non-upgradeable jobs', () => {
    render(
      <BatchUpgradeDialog
        open
        jobs={[
          {
            id: 'j1',
            name: 'Job 1',
            status: 'running',
            isWorkflowOutdated: true,
          },
          {
            id: 'j2',
            name: 'Job 2',
            status: 'completed',
            isWorkflowOutdated: false,
          },
        ]}
        onClose={vi.fn()}
        onConfirm={vi.fn().mockResolvedValue(undefined)}
      />
    )
    expect(screen.getByText('运行中')).toBeInTheDocument()
    expect(screen.getByText('已是最新版本')).toBeInTheDocument()
  })

  it('renders nothing when not open', () => {
    const { container } = render(
      <BatchUpgradeDialog
        open={false}
        jobs={[]}
        onClose={vi.fn()}
        onConfirm={vi.fn().mockResolvedValue(undefined)}
      />
    )
    expect(container.firstChild).toBeNull()
  })

  it('shows loading state on confirm and disables buttons', () => {
    render(
      <BatchUpgradeDialog
        open
        loading
        jobs={[
          {
            id: 'j1',
            name: 'Job 1',
            status: 'completed',
            isWorkflowOutdated: true,
          },
        ]}
        onClose={vi.fn()}
        onConfirm={vi.fn().mockResolvedValue(undefined)}
      />
    )
    expect(screen.getByText('升级中...')).toHaveAttribute('disabled')
    expect(screen.getByText('取消')).toHaveAttribute('disabled')
  })
})
