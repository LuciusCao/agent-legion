import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'

import { AddItemsDialog } from './AddItemsDialog'
import { api, createRun, fetchActiveWorkflowRevision } from '../api'
import { uploadMaterialFile } from '../lib/addItems'
import { useUiStore } from '../stores/uiStore'
import { TestQueryProvider } from '../testing/testQueryClient'
import type {
  ActiveWorkflowRevisionResponse,
  WorkflowDefinitionRecord,
} from '../types'

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
const mockFetchWorkflow = vi.mocked(fetchActiveWorkflowRevision)
const mockUpload = vi.mocked(uploadMaterialFile)

function renderWithClient(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

function activeRevisionOf(
  workflow: WorkflowDefinitionRecord
): ActiveWorkflowRevisionResponse {
  return {
    revision: {
      id: 'ws1:demo_workflow:v1',
      workspace_id: 'ws1',
      workflow_key: 'demo_workflow',
      version: 1,
      status: 'active',
      definition_hash: 'hash',
      created_at: '2026-01-01T00:00:00Z',
    },
    workflow,
    definition_yaml: '',
  }
}

function workflowWithModes(
  modes: { key: string; label: string; input_field: string }[]
) {
  return activeRevisionOf({
    key: 'demo_workflow',
    label: 'demo',
    intake: { modes },
    edges: [],
    nodes: [],
  })
}

function mockWorkspace() {
  mockApi.mockResolvedValue({
    workspace: {
      id: 'ws1',
      name: 'demo',
      default_workflow_key: 'demo_workflow',
    },
  } as never)
}

function pickFiles(testId: string, files: File[]) {
  fireEvent.change(screen.getByTestId(testId), { target: { files } })
}

describe('AddItemsDialog', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockCreateRun.mockReset()
    mockFetchWorkflow.mockReset()
    mockUpload.mockReset()
    useUiStore.setState({ toast: null, addDialogOpen: false })
    mockWorkspace()
    mockFetchWorkflow.mockResolvedValue(workflowWithModes([]))
  })

  it('renders both tabs', () => {
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    expect(screen.getByText('添加条目')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '上传材料' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '粘贴 ID' })).toBeInTheDocument()
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 0 个条目')
  })

  it('previews picked files with group counts and uploads them', async () => {
    mockUpload.mockResolvedValue({ materialId: 'm1', deduplicated: false })
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )

    pickFiles('add-items-file-input', [
      new File(['a'], 'a.txt', { type: 'text/plain' }),
      new File(['bb'], 'b.txt', { type: 'text/plain' }),
    ])

    expect(screen.getByTestId('upload-summary')).toHaveTextContent('文本 × 2')
    await waitFor(() => expect(mockUpload).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getAllByText('完成')).toHaveLength(2))
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 2 个条目')
  })

  it('marks failed uploads and retries them', async () => {
    mockUpload
      .mockRejectedValueOnce(new Error('网络错误'))
      .mockResolvedValueOnce({ materialId: 'm1', deduplicated: false })
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )

    pickFiles('add-items-file-input', [new File(['a'], 'a.txt')])

    await waitFor(() => expect(screen.getByText('失败')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(screen.getByText('完成')).toBeInTheDocument())
    expect(mockUpload).toHaveBeenCalledTimes(2)
  })

  it('parses pasted ids with dedup and shows the count', () => {
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    fireEvent.click(screen.getByRole('tab', { name: '粘贴 ID' }))
    fireEvent.change(screen.getByLabelText('外部 ID'), {
      target: { value: 'q1\n\nq2\nq1\n' },
    })
    expect(screen.getByTestId('ref-summary')).toHaveTextContent(
      '已解析 2 条引用'
    )
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 2 个条目')
  })

  it('submits merged material and ref items to the runs API', async () => {
    const onClose = vi.fn()
    mockUpload.mockResolvedValue({ materialId: 'm1', deduplicated: false })
    mockCreateRun.mockResolvedValue({
      run: { id: 'r1' },
      created_count: 2,
      jobs: [],
    } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )

    pickFiles('add-items-file-input', [new File(['a'], 'a.txt')])
    await waitFor(() => expect(screen.getByText('完成')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('tab', { name: '粘贴 ID' }))
    fireEvent.change(screen.getByLabelText('连接 Key'), {
      target: { value: 'cms' },
    })
    fireEvent.change(screen.getByLabelText('外部 ID'), {
      target: { value: 'q1' },
    })
    fireEvent.click(screen.getByRole('button', { name: '创建运行' }))

    await waitFor(() => expect(mockCreateRun).toHaveBeenCalledOnce())
    expect(mockCreateRun).toHaveBeenCalledWith('ws1', {
      workflow_key: 'demo_workflow',
      items: [
        { type: 'material', material_id: 'm1' },
        { type: 'ref', connection_key: 'cms', external_id: 'q1' },
      ],
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(useUiStore.getState().toast).toEqual({
      message: '运行已创建，共 2 个任务',
      type: 'success',
    })
  })

  it('shows the backend error when run creation fails', async () => {
    mockCreateRun.mockRejectedValue(new Error('全部条目被 dedup 过滤'))
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    fireEvent.click(screen.getByRole('tab', { name: '粘贴 ID' }))
    fireEvent.change(screen.getByLabelText('连接 Key'), {
      target: { value: 'cms' },
    })
    fireEvent.change(screen.getByLabelText('外部 ID'), {
      target: { value: 'q1' },
    })
    const submitButton = screen.getByRole('button', { name: '创建运行' })
    await waitFor(() => expect(submitButton).not.toBeDisabled())
    fireEvent.click(submitButton)

    await waitFor(() =>
      expect(useUiStore.getState().toast).toEqual({
        message: '创建运行失败: 全部条目被 dedup 过滤',
        type: 'error',
      })
    )
  })

  it('offers the legacy intake entry only when modes exist', async () => {
    mockFetchWorkflow.mockResolvedValue(
      workflowWithModes([
        {
          key: 'batch_by_ids',
          label: '按题目ID批量',
          input_field: 'question_ids',
        },
      ])
    )
    const onClose = vi.fn()
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )

    const legacyButton = await screen.findByRole('button', {
      name: '旧版接入模式',
    })
    fireEvent.click(legacyButton)

    expect(onClose).toHaveBeenCalled()
    expect(useUiStore.getState().addDialogOpen).toBe(true)
    expect(useUiStore.getState().addDialogWorkspaceId).toBe('ws1')
  })

  it('hides the legacy entry when the workflow has no intake modes', async () => {
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    await waitFor(() => expect(mockFetchWorkflow).toHaveBeenCalled())
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: '旧版接入模式' })
      ).not.toBeInTheDocument()
    )
  })
})
