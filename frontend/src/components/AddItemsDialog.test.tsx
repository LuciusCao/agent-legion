import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, act } from '@testing-library/react'
import type { ReactElement } from 'react'

import { AddItemsDialog } from './AddItemsDialog'
import { api, createRun, fetchActiveWorkflowRevision } from '../api'
import { uploadMaterialFile } from '../lib/addItems'
import { useUiStore } from '../stores/uiStore'
import { TestQueryProvider } from '../testing/testQueryClient'
import type { MaterialListResponse } from '../types'

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
const mockUpload = vi.mocked(uploadMaterialFile)
const mockFetchRevision = vi.mocked(fetchActiveWorkflowRevision)

function renderWithClient(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

let materialsResponse: MaterialListResponse = {
  materials: [],
  total: 0,
  limit: 0,
  offset: 0,
}

function mockWorkspace() {
  mockApi.mockImplementation(
    (path: unknown) =>
      Promise.resolve(
        String(path).includes('/materials')
          ? materialsResponse
          : {
              workspace: {
                id: 'ws1',
                name: 'demo',
                default_workflow_key: 'demo_workflow',
              },
            }
      ) as never
  )
}

function mockMaterials(materials: Record<string, unknown>[]) {
  materialsResponse = {
    materials,
    total: materials.length,
    limit: materials.length,
    offset: 0,
  } as MaterialListResponse
}

function pickFiles(testId: string, files: File[]) {
  fireEvent.change(screen.getByTestId(testId), { target: { files } })
}

describe('AddItemsDialog', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockCreateRun.mockReset()
    mockUpload.mockReset()
    mockFetchRevision.mockReset()
    // 默认：workspace 未发布 revision（404）→ 入口契约缺省全接受。
    mockFetchRevision.mockRejectedValue(
      Object.assign(new Error('No active workflow revision'), { status: 404 })
    )
    useUiStore.setState({ toast: null })
    mockMaterials([])
    mockWorkspace()
  })

  it('renders all three tabs', () => {
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    expect(screen.getByText('添加条目')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '上传材料' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '粘贴 ID' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '已有材料' })).toBeInTheDocument()
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

  it('lists only ready materials on the existing-materials tab', async () => {
    mockMaterials([
      {
        id: 'm-ready',
        filename: 'ready.md',
        size_bytes: 10,
        status: 'ready',
      },
      {
        id: 'm-pending',
        filename: 'pending.md',
        size_bytes: 20,
        status: 'pending_upload',
      },
    ])
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )

    fireEvent.click(screen.getByRole('tab', { name: '已有材料' }))

    await waitFor(() =>
      expect(screen.getByTestId('existing-materials-list')).toBeInTheDocument()
    )
    expect(screen.getByText('ready.md')).toBeInTheDocument()
    expect(screen.queryByText('pending.md')).not.toBeInTheDocument()
    expect(mockApi).toHaveBeenCalledWith(
      expect.stringContaining('/api/workspaces/ws1/materials')
    )
  })

  it('counts checked existing materials in the total', async () => {
    mockMaterials([
      { id: 'm1', filename: 'a.md', size_bytes: 10, status: 'ready' },
      { id: 'm2', filename: 'b.md', size_bytes: 20, status: 'ready' },
    ])
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )

    fireEvent.click(screen.getByRole('tab', { name: '已有材料' }))
    await waitFor(() =>
      expect(screen.getByTestId('existing-materials-list')).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('checkbox', { name: 'a.md' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'b.md' }))

    expect(screen.getByTestId('total-count')).toHaveTextContent('共 2 个条目')

    fireEvent.click(screen.getByRole('checkbox', { name: 'a.md' }))
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 1 个条目')
  })

  it('merges uploaded, existing and ref items in the submit payload', async () => {
    const onClose = vi.fn()
    mockUpload.mockResolvedValue({ materialId: 'm-up', deduplicated: false })
    mockMaterials([
      { id: 'm-old', filename: 'old.md', size_bytes: 10, status: 'ready' },
    ])
    mockCreateRun.mockResolvedValue({
      run: { id: 'r1' },
      created_count: 3,
      jobs: [],
    } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )

    pickFiles('add-items-file-input', [new File(['a'], 'a.txt')])
    await waitFor(() => expect(screen.getByText('完成')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('tab', { name: '已有材料' }))
    await waitFor(() =>
      expect(screen.getByTestId('existing-materials-list')).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('checkbox', { name: 'old.md' }))

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
        { type: 'material', material_id: 'm-up' },
        { type: 'material', material_id: 'm-old' },
        { type: 'ref', connection_key: 'cms', external_id: 'q1' },
      ],
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  function mockRevisionWithAcceptedTypes(accepted: string[]) {
    mockFetchRevision.mockResolvedValue({
      definition_yaml: '',
      revision: { id: 'r1', version: 1 },
      workflow: {
        key: 'demo_workflow',
        label: 'demo',
        intake: { modes: [] },
        nodes: [
          {
            key: '_start',
            label: '入口',
            capability: '',
            node_type: 'start',
            accepted_item_types: accepted,
            after: [],
            inputs: [],
            outputs: [],
          },
        ],
        edges: [],
      },
    } as never)
  }

  it('disables the ref tab when the start node accepts materials only', async () => {
    mockRevisionWithAcceptedTypes(['material'])
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )

    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '粘贴 ID' })).toBeDisabled()
    )
    expect(screen.getByRole('tab', { name: '上传材料' })).toBeEnabled()
    expect(screen.getByRole('tab', { name: '已有材料' })).toBeEnabled()
    expect(screen.getByTestId('item-type-hint')).toHaveTextContent('材料条目')
  })

  it('disables the material tabs when the start node accepts refs only', async () => {
    mockRevisionWithAcceptedTypes(['ref'])
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )

    // 默认选中的 upload tab 被禁用后应落到可用的 ref tab。
    await waitFor(() =>
      expect(screen.getByLabelText('外部 ID')).toBeInTheDocument()
    )
    expect(screen.getByRole('tab', { name: '上传材料' })).toBeDisabled()
    expect(screen.getByRole('tab', { name: '已有材料' })).toBeDisabled()
    expect(screen.getByRole('tab', { name: '粘贴 ID' })).toBeEnabled()
    expect(screen.getByTestId('item-type-hint')).toHaveTextContent(
      '外部引用条目'
    )
  })

  it('drops hidden-panel items when the resolved contract narrows', async () => {
    // 竞态：契约查询未 resolve 时缺省全接受，用户已粘贴 ref id 并完成
    // 上传；契约随后 resolve 为仅 material——隐藏面板残留的 ref 条目
    // 不计数、不提交。
    let resolveRevision!: (value: unknown) => void
    mockFetchRevision.mockReturnValue(
      new Promise((resolve) => {
        resolveRevision = resolve
      }) as never
    )
    mockUpload.mockResolvedValue({ materialId: 'm1', deduplicated: false })
    mockCreateRun.mockResolvedValue({
      run: { id: 'r1' },
      created_count: 1,
      jobs: [],
    } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
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
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 2 个条目')

    await act(async () => {
      resolveRevision({
        definition_yaml: '',
        revision: { id: 'r1', version: 1 },
        workflow: {
          key: 'demo_workflow',
          label: 'demo',
          intake: { modes: [] },
          nodes: [
            {
              key: '_start',
              label: '入口',
              capability: '',
              node_type: 'start',
              accepted_item_types: ['material'],
              after: [],
              inputs: [],
              outputs: [],
            },
          ],
          edges: [],
        },
      })
    })

    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '粘贴 ID' })).toBeDisabled()
    )
    // 残留的 ref 条目不再计数，剩下的上传条目仍可提交。
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 1 个条目')
    fireEvent.click(screen.getByRole('button', { name: '创建运行' }))

    await waitFor(() => expect(mockCreateRun).toHaveBeenCalledOnce())
    expect(mockCreateRun).toHaveBeenCalledWith('ws1', {
      workflow_key: 'demo_workflow',
      items: [{ type: 'material', material_id: 'm1' }],
    })
  })
})
