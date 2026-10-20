import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'

import { AddItemsDialog } from './AddItemsDialog'
import { api, createRun, fetchActiveWorkflowRevision } from '../api'
import { timeoutError } from '../api/requestTimeout'
import { uploadMaterialFile } from '../lib/addItems'
import { useUiStore } from '../stores/uiStore'
import { TestQueryProvider } from '../testing/testQueryClient'

vi.mock('../api', () => ({
  api: vi.fn(),
  createRun: vi.fn(),
  fetchActiveWorkflowRevision: vi.fn(),
}))

vi.mock('../lib/addItems', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/addItems')>()
  return { ...actual, uploadMaterialFile: vi.fn() }
})

const mockApi = vi.mocked(api)
const mockCreateRun = vi.mocked(createRun)
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

/** 粘贴一条 ref 并提交（等按钮可用再点——workspace 查询未落地时按钮禁用；
 * 点击包进 async act：提交是异步 handler，状态更新须落在 act 内）。 */
async function submitRefIds(ids: string) {
  fireEvent.click(screen.getByRole('tab', { name: '粘贴 ID' }))
  fireEvent.change(screen.getByLabelText('连接 Key'), {
    target: { value: 'cms' },
  })
  fireEvent.change(screen.getByLabelText('外部 ID'), { target: { value: ids } })
  const submitButton = screen.getByRole('button', { name: '创建运行' })
  await waitFor(() => expect(submitButton).not.toBeDisabled())
  await act(async () => {
    fireEvent.click(submitButton)
  })
}

describe('AddItemsDialog 提交兜底（#1157 phase0）', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockCreateRun.mockReset()
    mockFetchRevision.mockReset()
    vi.mocked(uploadMaterialFile).mockReset()
    // 默认：workspace 未发布 revision（404）→ 入口契约按 DEFAULT 处理。
    mockFetchRevision.mockRejectedValue(
      Object.assign(new Error('No active workflow revision'), { status: 404 })
    )
    useUiStore.setState({ toast: null })
    mockWorkspace()
  })

  it('shows created and skipped counts in the success toast', async () => {
    mockCreateRun.mockResolvedValue({
      run: { id: 'r1' },
      created_count: 1,
      skipped_count: 1,
    } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    await submitRefIds('q1\nq2')

    await waitFor(() =>
      expect(useUiStore.getState().toast).toEqual({
        message: '已开始运行：新建 1 个任务；1 个条目已有任务，自动跳过。',
        type: 'success',
      })
    )
  })

  it('makes the all-duplicate 400 readable without touching the machine contract', async () => {
    const onClose = vi.fn()
    mockCreateRun.mockRejectedValue(
      Object.assign(new Error('No tasks were resolved from input'), {
        status: 400,
      })
    )
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    await submitRefIds('q1')

    // 计数取本地提交数——400 语义即本次 0 新建，机器契约字符串不变。
    await waitFor(() =>
      expect(useUiStore.getState().toast).toEqual({
        message: '这 1 个条目都已经有任务了，无需重复创建。',
        type: 'error',
      })
    )
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not push a blind retry when the submission times out', async () => {
    const onClose = vi.fn()
    mockCreateRun.mockRejectedValue(timeoutError(120_000))
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    await submitRefIds('q1')

    await waitFor(() =>
      expect(useUiStore.getState().toast).toEqual({
        message:
          '网络较慢，暂时没收到结果。请稍后刷新任务列表查看；没出现的话再重新提交，已经提交的条目不会重复创建。',
        type: 'error',
      })
    )
    // 不自动重试、不关对话框、输入保留——用户可稍后确认列表再决定。
    expect(onClose).not.toHaveBeenCalled()
    expect(mockCreateRun).toHaveBeenCalledOnce()
    expect(screen.getByLabelText('外部 ID')).toHaveValue('q1')
  })

  it('shows the same fallback wording on a network failure', async () => {
    // fetch 的网络层失败（连接断开/DNS/服务端假死）在前端是 TypeError。
    mockCreateRun.mockRejectedValue(new TypeError('Failed to fetch'))
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    await submitRefIds('q1')

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain(
        '网络较慢，暂时没收到结果'
      )
    )
  })

  it('keeps waiting for a slow server and lands the late response (故障注入)', async () => {
    // 人为拖慢服务端：createRun 悬挂直到测试手动 resolve（等信号非等时长）。
    let resolveRun!: (value: unknown) => void
    mockCreateRun.mockReturnValue(
      new Promise((resolve) => {
        resolveRun = resolve
      }) as never
    )
    const onClose = vi.fn()
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    await submitRefIds('q1')

    // 等待期间：按钮处于「处理中」且禁用、对话框不关、无报错 toast。
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '处理中...' })).toBeDisabled()
    )
    expect(onClose).not.toHaveBeenCalled()
    expect(useUiStore.getState().toast).toBeNull()

    // 迟到响应到达：仍按成功落地并关闭对话框。
    await act(async () => {
      resolveRun({ run: { id: 'r1' }, created_count: 1, skipped_count: 0 })
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(useUiStore.getState().toast).toEqual({
      message: '运行已创建，共 1 个任务',
      type: 'success',
    })
  })

  it('shows an explicit acceptance hint for submissions over 1000 items', () => {
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    fireEvent.click(screen.getByRole('tab', { name: '粘贴 ID' }))
    const pasteIds = (count: number) =>
      fireEvent.change(screen.getByLabelText('外部 ID'), {
        target: {
          value: Array.from({ length: count }, (_, i) => `q${i}`).join('\n'),
        },
      })

    pasteIds(1001)
    expect(screen.getByTestId('total-count')).toHaveTextContent(
      '共 1001 个条目'
    )
    const hint = screen.getByTestId('bulk-submit-hint')
    expect(hint).toHaveTextContent('条目较多')
    expect(hint).toHaveTextContent('每批不超过 1000 条')

    // 边界：恰好 1000 条不提示（>1000 才算大批量）。
    pasteIds(1000)
    expect(screen.getByTestId('total-count')).toHaveTextContent(
      '共 1000 个条目'
    )
    expect(screen.queryByTestId('bulk-submit-hint')).toBeNull()
  })
})
