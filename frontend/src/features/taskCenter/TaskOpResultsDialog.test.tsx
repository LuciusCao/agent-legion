import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { TaskOpResultsDialog } from './TaskOpResultsDialog'
import type { JobBatchOperationResultsPage } from '../../api/jobBatchOperationsApi'

const fetchNextPage = vi.fn()

let mockState: {
  data?: { pages: JobBatchOperationResultsPage[] }
  isLoading: boolean
  isError: boolean
  error?: unknown
  hasNextPage: boolean
  isFetchingNextPage: boolean
}

vi.mock('./useJobBatchOps', () => ({
  useJobBatchOpResults: () => ({
    ...mockState,
    fetchNextPage,
  }),
}))

function page(
  results: { seq: number; result: Record<string, unknown> }[],
  nextCursor: string | null = null
): JobBatchOperationResultsPage {
  return { results, next_cursor: nextCursor }
}

describe('TaskOpResultsDialog（#946 / M52）', () => {
  it('renders paged per-job results with translated reason labels', () => {
    mockState = {
      data: {
        pages: [
          page([
            {
              seq: 0,
              result: {
                job_id: 'job-aaaa-bbbb',
                status: 'succeeded',
                reason_code: null,
                message: null,
              },
            },
            {
              seq: 1,
              result: {
                job_id: 'job-cccc-dddd',
                status: 'skipped',
                reason_code: 'already_paused',
                message: 'Job is paused',
              },
            },
          ]),
        ],
      },
      isLoading: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
    }
    render(
      <TaskOpResultsDialog
        workspaceId="ws1"
        opId="op1"
        title="批量暂停"
        open
        onClose={() => {}}
      />
    )
    expect(screen.getByText('批量暂停 · 逐条结果')).toBeTruthy()
    expect(screen.getByText('成功')).toBeTruthy()
    expect(screen.getByText('跳过')).toBeTruthy()
    expect(screen.getByText('已处于暂停')).toBeTruthy()
    expect(screen.getByText('Job is paused')).toBeTruthy()
    expect(screen.queryByText('加载更多')).toBeNull()
  })

  it('paginates via 加载更多 when a next cursor exists', () => {
    mockState = {
      data: { pages: [page([{ seq: 0, result: { job_id: 'j' } }], '0')] },
      isLoading: false,
      isError: false,
      hasNextPage: true,
      isFetchingNextPage: false,
    }
    render(
      <TaskOpResultsDialog
        workspaceId="ws1"
        opId="op1"
        title="批量重跑"
        open
        onClose={() => {}}
      />
    )
    fireEvent.click(screen.getByText('加载更多'))
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
  })

  it('shows the empty and loading hints', () => {
    mockState = {
      data: { pages: [page([])] },
      isLoading: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
    }
    render(
      <TaskOpResultsDialog
        workspaceId="ws1"
        opId="op1"
        title="批量删除"
        open
        onClose={() => {}}
      />
    )
    expect(screen.getByText('没有逐条结果记录。')).toBeTruthy()
  })
})
