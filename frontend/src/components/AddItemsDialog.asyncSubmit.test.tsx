import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'

import { AddItemsDialog } from './AddItemsDialog'
import {
  api,
  createRun,
  createRunAsync,
  fetchActiveWorkflowRevision,
} from '../api'
import { useUiStore } from '../stores/uiStore'
import { useTaskCenterStore } from '../stores/taskCenterStore'
import { TestQueryProvider } from '../testing/testQueryClient'

vi.mock('../api', () => ({
  api: vi.fn(),
  createRun: vi.fn(),
  createRunAsync: vi.fn(),
  fetchActiveWorkflowRevision: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockCreateRun = vi.mocked(createRun)
const mockCreateRunAsync = vi.mocked(createRunAsync)
const mockFetchRevision = vi.mocked(fetchActiveWorkflowRevision)

function renderWithClient(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

function mockWorkspace() {
  mockApi.mockImplementation(
    (path: unknown) =>
      Promise.resolve(
        String(path).includes('/materials')
          ? { materials: [], total: 0, limit: 0, offset: 0 }
          : { workspace: { id: 'ws1', name: 'demo' } }
      ) as never
  )
}

/** 粘贴 N 个 ref id 并提交（等按钮可用再点；点击包进 async act）。 */
async function submitRefIdCount(count: number) {
  fireEvent.click(screen.getByRole('tab', { name: '粘贴 ID' }))
  fireEvent.change(screen.getByLabelText('连接 Key'), {
    target: { value: 'cms' },
  })
  fireEvent.change(screen.getByLabelText('外部 ID'), {
    target: {
      value: Array.from({ length: count }, (_, i) => `q${i}`).join('\n'),
    },
  })
  const submitButton = screen.getByRole('button', { name: '创建运行' })
  await waitFor(() => expect(submitButton).not.toBeDisabled())
  await act(async () => {
    fireEvent.click(submitButton)
  })
}

describe('AddItemsDialog 大批量异步受理（#1157 phase3）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetchRevision.mockRejectedValue(
      Object.assign(new Error('No active workflow revision'), { status: 404 })
    )
    useUiStore.setState({ toast: null })
    useTaskCenterStore.setState({ acceptedNotice: null })
    mockWorkspace()
  })

  it('switches to async accept over 1000 items: 202 句柄 → 受理通知 + 关对话框', async () => {
    const onClose = vi.fn()
    mockCreateRunAsync.mockResolvedValue({
      run: { id: 'run-digest-1' },
      batch: { run_id: 'run-digest-1', status: 'pending' },
    } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    await submitRefIdCount(1001)

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    // 异步路径：createRunAsync 被调一次，同步 createRun 不被调。
    expect(mockCreateRunAsync).toHaveBeenCalledOnce()
    expect(mockCreateRunAsync).toHaveBeenCalledWith(
      'ws1',
      expect.objectContaining({ items: expect.arrayContaining([]) })
    )
    expect(mockCreateRun).not.toHaveBeenCalled()
    // 秒级受理反馈：受理通知携带句柄与条目数（TaskAcceptToast 消费），
    // 不再走瞬时成功 toast。
    expect(useTaskCenterStore.getState().acceptedNotice).toEqual({
      workspaceId: 'ws1',
      itemsTotal: 1001,
      runId: 'run-digest-1',
    })
    expect(useUiStore.getState().toast).toBeNull()
  })

  it('keeps the sync fast path at exactly 1000 items（小批量不回退）', async () => {
    const onClose = vi.fn()
    mockCreateRun.mockResolvedValue({
      run: { id: 'r1' },
      created_count: 1000,
      skipped_count: 0,
    } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    await submitRefIdCount(1000)

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(mockCreateRun).toHaveBeenCalledOnce()
    expect(mockCreateRunAsync).not.toHaveBeenCalled()
    expect(useTaskCenterStore.getState().acceptedNotice).toBeNull()
    expect(useUiStore.getState().toast).toEqual({
      message: '运行已创建，共 1000 个任务',
      type: 'success',
    })
  })

  it('surfaces async accept failures with the shared fallback wording', async () => {
    const onClose = vi.fn()
    mockCreateRunAsync.mockRejectedValue(
      Object.assign(new Error('Workspace has no active workflow revision'), {
        status: 400,
      })
    )
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    await submitRefIdCount(1001)

    await waitFor(() => expect(useUiStore.getState().toast?.type).toBe('error'))
    expect(useUiStore.getState().toast?.message).toContain('创建运行失败')
    // 失败不关对话框、不发受理通知——条目仍在，可修正后重提。
    expect(onClose).not.toHaveBeenCalled()
    expect(useTaskCenterStore.getState().acceptedNotice).toBeNull()
  })
})
