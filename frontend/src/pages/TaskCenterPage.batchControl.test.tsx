import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'

import TaskCenterPage from './TaskCenterPage'
import { listRunBatches } from '../api/runBatchesApi'
import {
  cancelJobBatchOperation,
  pauseJobBatchOperation,
  resumeJobBatchOperation,
} from '../api/jobBatchOperationControlApi'
import { listJobBatchOperations } from '../api/jobBatchOperationsListApi'
import { useUiStore } from '../stores/uiStore'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { TestQueryProvider } from '../testing/testQueryClient'

vi.mock('../api/runBatchesApi', () => ({
  listRunBatches: vi.fn(),
  archiveRunBatch: vi.fn(),
  pauseRunBatch: vi.fn(),
  resumeRunBatch: vi.fn(),
  cancelRunBatch: vi.fn(),
}))

vi.mock('../api/jobBatchOperationsApi', () => ({
  getJobBatchOperation: vi.fn(),
  listJobBatchOperationResults: vi.fn(),
}))

vi.mock('../api/jobBatchOperationControlApi', () => ({
  pauseJobBatchOperation: vi.fn(),
  resumeJobBatchOperation: vi.fn(),
  cancelJobBatchOperation: vi.fn(),
}))

vi.mock('../api/jobBatchOperationsListApi', () => ({
  listJobBatchOperations: vi.fn(),
  archiveJobBatchOperation: vi.fn(),
}))

vi.mock('../hooks/useWorkerPausedStatus', () => ({
  useWorkerPausedStatus: () => ({ data: false }),
}))

const mockRunList = vi.mocked(listRunBatches)
const mockOpList = vi.mocked(listJobBatchOperations)
const mockPause = vi.mocked(pauseJobBatchOperation)
const mockResume = vi.mocked(resumeJobBatchOperation)
const mockCancel = vi.mocked(cancelJobBatchOperation)

function op(overrides: Record<string, unknown> = {}) {
  return {
    id: 'op-rerun-1',
    workspace_id: 'ws1',
    kind: 'rerun',
    status: 'running',
    items_total: 20000,
    processed_count: 8400,
    succeeded_count: 8000,
    skipped_count: 300,
    failed_count: 100,
    reason_counts: {},
    output: {},
    chunk_errors: [],
    error_message: '',
    created_by: 'tester',
    created_at: '2026-10-21T02:21:00+00:00',
    updated_at: '2026-10-21T02:24:00+00:00',
    finished_at: null,
    archived_at: null,
    archive_source: null,
    ...overrides,
  }
}

function opPage(operations: unknown[], activeCount = 0) {
  return { operations, next_cursor: null, active_count: activeCount }
}

function mockViews(active: unknown[], ended: unknown[] = []) {
  mockOpList.mockImplementation((_ws, options: { view: string }) => {
    if (options.view === 'active')
      return Promise.resolve(opPage(active, active.length) as never)
    if (options.view === 'ended') return Promise.resolve(opPage(ended) as never)
    return Promise.resolve(opPage([]) as never)
  })
}

function renderPage() {
  return render(
    <TestQueryProvider>
      <MemoryRouter initialEntries={['/workspaces/ws1/tasks']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/tasks"
            element={<TaskCenterPage />}
          />
        </Routes>
      </MemoryRouter>
    </TestQueryProvider>
  )
}

function activeRequestCount() {
  return mockOpList.mock.calls.filter(
    ([, options]) => (options as { view: string }).view === 'active'
  ).length
}

describe('TaskCenterPage job batch controls（#1397）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useUiStore.setState({ toast: null })
    mockRunList.mockResolvedValue({
      batches: [],
      next_cursor: null,
      active_count: 0,
    } as never)
    mockViews([])
  })

  it('guards pause with confirmation and blocks duplicate submits while busy', async () => {
    mockViews([op()])
    let resolvePause!: () => void
    mockPause.mockReturnValue(
      new Promise<void>((resolve) => {
        resolvePause = resolve
      }) as never
    )
    renderPage()

    const card = await screen.findByTestId('task-card-op-rerun-1')
    fireEvent.click(within(card).getByRole('button', { name: '暂停' }))
    expect(mockPause).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('暂停批量重跑？')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('暂停后不再处理剩余 11,600 条')
    expect(dialog).toHaveTextContent('已开始处理的条目可能仍会完成')
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(mockPause).not.toHaveBeenCalled()

    fireEvent.click(within(card).getByRole('button', { name: '暂停' }))
    const requestsBefore = activeRequestCount()
    fireEvent.click(screen.getByRole('button', { name: '确认暂停' }))
    const busy = await screen.findByRole('button', { name: '暂停中…' })
    expect(busy).toBeDisabled()
    fireEvent.click(busy)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(mockPause).toHaveBeenCalledTimes(1)
    expect(mockPause).toHaveBeenCalledWith('ws1', 'op-rerun-1')

    resolvePause()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() =>
      expect(activeRequestCount()).toBeGreaterThan(requestsBefore)
    )
    expect(useUiStore.getState().toast).toEqual({
      message: '已暂停。后续处理已挂起，已处理结果保留。',
      type: 'success',
    })
  })

  it('resumes directly and moves a cancelled op to ended with preserved results', async () => {
    let cancelled = false
    mockOpList.mockImplementation((_ws, options: { view: string }) => {
      if (options.view === 'active')
        return Promise.resolve(
          opPage(
            cancelled ? [] : [op({ status: 'paused' })],
            cancelled ? 0 : 1
          ) as never
        )
      if (options.view === 'ended')
        return Promise.resolve(
          opPage(
            cancelled
              ? [
                  op({
                    status: 'cancelled',
                    finished_at: '2026-10-21T02:30:00+00:00',
                  }),
                ]
              : []
          ) as never
        )
      return Promise.resolve(opPage([]) as never)
    })
    mockResume.mockResolvedValue(op({ status: 'running' }) as never)
    mockCancel.mockImplementation(() => {
      cancelled = true
      return Promise.resolve(op({ status: 'cancelled' }) as never)
    })
    renderPage()

    const pausedCard = await screen.findByTestId('task-card-op-rerun-1')
    fireEvent.click(within(pausedCard).getByRole('button', { name: '继续' }))
    await waitFor(() =>
      expect(mockResume).toHaveBeenCalledWith('ws1', 'op-rerun-1')
    )
    expect(useUiStore.getState().toast).toEqual({
      message: '已继续处理。',
      type: 'success',
    })

    fireEvent.click(within(pausedCard).getByRole('button', { name: '取消' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('取消批量重跑？')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('取消后不可恢复')
    expect(dialog).toHaveTextContent('结果保留，不会撤销或回滚')
    fireEvent.click(within(dialog).getByRole('button', { name: '确认取消' }))

    await screen.findByText('已取消')
    const endedCard = screen.getByTestId('task-card-op-rerun-1')
    expect(endedCard).toHaveTextContent('取消前已处理 8,400 / 20,000')
    expect(within(endedCard).queryByRole('button', { name: '继续' })).toBeNull()
    expect(
      within(endedCard).getByRole('button', { name: '逐条结果' })
    ).toBeInTheDocument()
    expect(
      within(endedCard).getByRole('button', { name: '归档' })
    ).toBeInTheDocument()
    expect(useUiStore.getState().toast).toEqual({
      message: '已取消。未处理部分已停止，已处理结果保留。',
      type: 'success',
    })
  })

  it('refreshes and reports a 409 instead of silently succeeding', async () => {
    mockViews([op()])
    mockPause.mockRejectedValue(
      Object.assign(new Error('operation is already paused'), { status: 409 })
    )
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    renderPage()

    const card = await screen.findByTestId('task-card-op-rerun-1')
    fireEvent.click(within(card).getByRole('button', { name: '暂停' }))
    const requestsBefore = activeRequestCount()
    fireEvent.click(screen.getByRole('button', { name: '确认暂停' }))

    await waitFor(() =>
      expect(useUiStore.getState().toast).toEqual({
        message: '操作状态已变化，已刷新最新进度。',
        type: 'error',
      })
    )
    await waitFor(() =>
      expect(activeRequestCount()).toBeGreaterThan(requestsBefore)
    )
    expect(screen.queryByRole('dialog')).toBeNull()
    consoleError.mockRestore()
  })

  it('uses the same control channel for bulk approval and preserves approved items wording', async () => {
    mockViews([op({ id: 'op-approve-1', kind: 'approve' })])
    mockCancel.mockResolvedValue(
      op({ id: 'op-approve-1', kind: 'approve', status: 'cancelled' }) as never
    )
    renderPage()

    const card = await screen.findByTestId('task-card-op-approve-1')
    fireEvent.click(within(card).getByRole('button', { name: '取消' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('取消批量通过？')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('已通过的审批不会撤销。')
    fireEvent.click(within(dialog).getByRole('button', { name: '确认取消' }))

    await waitFor(() =>
      expect(mockCancel).toHaveBeenCalledWith('ws1', 'op-approve-1')
    )
    expect(useUiStore.getState().toast).toEqual({
      message: '已取消。未处理部分已停止，已处理结果保留。',
      type: 'success',
    })
  })
})
