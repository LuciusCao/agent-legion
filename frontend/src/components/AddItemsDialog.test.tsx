import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, act } from '@testing-library/react'
import type { ReactElement } from 'react'

import { AddItemsDialog } from './AddItemsDialog'
import { api, createRun, fetchActiveWorkflowRevision } from '../api'
import { createMaterialBundle } from '../api/materialsApi'
import {
  createCampaignFromManifest,
  createSubmitCampaign,
} from '../api/campaignApi'
import { readFileText, uploadMaterialFile } from '../lib/addItems'
import { useUiStore } from '../stores/uiStore'
import { TestQueryProvider } from '../testing/testQueryClient'
import type { MaterialListResponse } from '../types'

vi.mock('../api', () => ({
  api: vi.fn(),
  createRun: vi.fn(),
  fetchActiveWorkflowRevision: vi.fn(),
}))

vi.mock('../api/materialsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/materialsApi')>()
  return { ...actual, createMaterialBundle: vi.fn() }
})

// #532 PR-D：粘贴 ID / 清单文件通道创建批量任务（submit campaign）。
vi.mock('../api/campaignApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/campaignApi')>()
  return {
    ...actual,
    createSubmitCampaign: vi.fn(),
    createCampaignFromManifest: vi.fn(),
  }
})

vi.mock('../lib/addItems', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/addItems')>()
  return { ...actual, uploadMaterialFile: vi.fn() }
})

const mockApi = vi.mocked(api)
const mockCreateRun = vi.mocked(createRun)
const mockCreateBundle = vi.mocked(createMaterialBundle)
const mockCreateSubmitCampaign = vi.mocked(createSubmitCampaign)
const mockCreateCampaignFromManifest = vi.mocked(createCampaignFromManifest)
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

function folderFile(relativePath: string, content: string) {
  const file = new File([content], relativePath.split('/').pop()!)
  Object.defineProperty(file, 'webkitRelativePath', { value: relativePath })
  return file
}

function campaignResponse() {
  return {
    campaign: {
      id: 'camp1',
      workspace_id: 'ws1',
      mode: 'submit',
      status: 'pending',
      name: '',
      target_spec: {},
      progress: {},
      watermark: 30000,
      batch_size: 5000,
      batches_submitted: 0,
      jobs_succeeded: 0,
      jobs_skipped: 0,
      jobs_failed: 0,
      error_message: '',
      created_by: '',
      created_at: '2026-09-09T00:00:00Z',
      updated_at: '2026-09-09T00:00:00Z',
      finished_at: null,
    },
  }
}

describe('AddItemsDialog', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockCreateRun.mockReset()
    mockCreateBundle.mockReset()
    mockCreateSubmitCampaign.mockReset()
    mockCreateCampaignFromManifest.mockReset()
    mockUpload.mockReset()
    mockFetchRevision.mockReset()
    // 默认：workspace 未发布 revision（404）→ 入口契约按 DEFAULT
    // ['material','ref'] 处理（刻意不含 bundle，存量 fail-closed）。
    mockFetchRevision.mockRejectedValue(
      Object.assign(new Error('No active workflow revision'), { status: 404 })
    )
    mockCreateSubmitCampaign.mockResolvedValue(campaignResponse() as never)
    mockCreateCampaignFromManifest.mockResolvedValue(
      campaignResponse() as never
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

  it('creates a submit campaign for pasted ids of any row count', async () => {
    // #532 PR-D 定稿：粘贴 ID 任意行数都创建批量任务（无大小分界，
    // 用户对分批无感）；不再是 createRun。
    const onClose = vi.fn()
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    fireEvent.click(screen.getByRole('tab', { name: '粘贴 ID' }))
    fireEvent.change(screen.getByLabelText('连接 Key'), {
      target: { value: 'cms' },
    })
    fireEvent.change(screen.getByLabelText('外部 ID'), {
      target: { value: 'q1\nq2\nq3' },
    })
    const submitButton = screen.getByRole('button', { name: '添加' })
    await waitFor(() => expect(submitButton).not.toBeDisabled())
    fireEvent.click(submitButton)

    await waitFor(() => expect(mockCreateSubmitCampaign).toHaveBeenCalledOnce())
    expect(mockCreateSubmitCampaign).toHaveBeenCalledWith('ws1', {
      items: [
        { type: 'ref', connection_key: 'cms', external_id: 'q1' },
        { type: 'ref', connection_key: 'cms', external_id: 'q2' },
        { type: 'ref', connection_key: 'cms', external_id: 'q3' },
      ],
    })
    expect(mockCreateRun).not.toHaveBeenCalled()
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(useUiStore.getState().toast).toEqual({
      message:
        '批量任务已创建，共 3 个条目将按执行节奏自动创建任务，进度可在「批量任务」页查看',
      type: 'success',
    })
  })

  it('uploads a manifest file through the campaign upload channel', async () => {
    // 文件清单通道：裸 ID 行（csv 取首列）规整为 ref 条目，multipart 上传。
    const onClose = vi.fn()
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    fireEvent.click(screen.getByRole('tab', { name: '粘贴 ID' }))
    fireEvent.change(screen.getByLabelText('连接 Key'), {
      target: { value: 'cms' },
    })
    pickFiles('add-items-manifest-input', [
      new File(['Q-1001\nQ-1002\n'], 'ids.csv', { type: 'text/csv' }),
    ])
    await waitFor(() =>
      expect(screen.getByTestId('manifest-summary')).toHaveTextContent(
        '解析 2 条'
      )
    )
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 2 个条目')
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() =>
      expect(mockCreateCampaignFromManifest).toHaveBeenCalledOnce()
    )
    const [workspaceId, payload] = mockCreateCampaignFromManifest.mock.calls[0]
    expect(workspaceId).toBe('ws1')
    const text = await readFileText(payload as File)
    expect(text.split('\n').filter(Boolean)).toEqual([
      JSON.stringify({
        type: 'ref',
        connection_key: 'cms',
        external_id: 'Q-1001',
      }),
      JSON.stringify({
        type: 'ref',
        connection_key: 'cms',
        external_id: 'Q-1002',
      }),
    ])
    expect(mockCreateSubmitCampaign).not.toHaveBeenCalled()
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('merges a manifest file with pasted ids into one upload (no silent drop)', async () => {
    // 审核 P1 回归锁：清单文件与粘贴 ID 并存时，multipart payload 必须
    // 含两者——旧代码只上传文件 payload，粘贴的 ID 被静默丢弃而 toast
    // 按合并口径报成功。
    const onClose = vi.fn()
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    fireEvent.click(screen.getByRole('tab', { name: '粘贴 ID' }))
    fireEvent.change(screen.getByLabelText('连接 Key'), {
      target: { value: 'cms' },
    })
    // 粘贴 1 个 ID + 上传 2 行清单文件
    fireEvent.change(screen.getByLabelText('外部 ID'), {
      target: { value: 'q-extra' },
    })
    pickFiles('add-items-manifest-input', [
      new File(['Q-1001\nQ-1002\n'], 'ids.csv', { type: 'text/csv' }),
    ])
    await waitFor(() =>
      expect(screen.getByTestId('manifest-summary')).toHaveTextContent(
        '解析 2 条'
      )
    )
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 3 个条目')
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() =>
      expect(mockCreateCampaignFromManifest).toHaveBeenCalledOnce()
    )
    const [, payload] = mockCreateCampaignFromManifest.mock.calls[0]
    const lines = (await readFileText(payload as File))
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line) as { external_id?: string })
    expect(lines.map((l) => l.external_id)).toEqual([
      'Q-1001',
      'Q-1002',
      'q-extra',
    ])
    expect(mockCreateSubmitCampaign).not.toHaveBeenCalled()
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('submits materials-only through the direct run path', async () => {
    // 材料上传 tab 保持现状：无粘贴 ID / 清单时直接创建运行。
    const onClose = vi.fn()
    mockUpload.mockResolvedValue({ materialId: 'm1', deduplicated: false })
    mockCreateRun.mockResolvedValue({
      run: { id: 'r1' },
      created_count: 1,
    } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )
    pickFiles('add-items-file-input', [new File(['a'], 'a.txt')])
    await waitFor(() => expect(screen.getByText('完成')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '创建运行' }))

    await waitFor(() => expect(mockCreateRun).toHaveBeenCalledOnce())
    expect(mockCreateRun).toHaveBeenCalledWith('ws1', {
      workflow_key: 'demo_workflow',
      items: [{ type: 'material', material_id: 'm1' }],
    })
    expect(mockCreateSubmitCampaign).not.toHaveBeenCalled()
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('merges uploaded materials with pasted ids into the campaign list', async () => {
    // 混填（材料 + 粘贴 ID）：整单进批量任务（清单含全部条目）。
    const onClose = vi.fn()
    mockUpload.mockResolvedValue({ materialId: 'm1', deduplicated: false })
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
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => expect(mockCreateSubmitCampaign).toHaveBeenCalledOnce())
    expect(mockCreateSubmitCampaign).toHaveBeenCalledWith('ws1', {
      items: [
        { type: 'material', material_id: 'm1' },
        { type: 'ref', connection_key: 'cms', external_id: 'q1' },
      ],
    })
    expect(mockCreateRun).not.toHaveBeenCalled()
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('shows the backend error when campaign creation fails', async () => {
    mockCreateSubmitCampaign.mockRejectedValue(
      new Error('全部条目被 dedup 过滤')
    )
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
    const submitButton = screen.getByRole('button', { name: '添加' })
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

  it('keeps existing-materials selection on the direct run path', async () => {
    // 已有材料（无粘贴 ID）：维持直接创建运行。
    mockMaterials([
      { id: 'm-old', filename: 'old.md', size_bytes: 10, status: 'ready' },
    ])
    mockCreateRun.mockResolvedValue({
      run: { id: 'r1' },
      created_count: 1,
    } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    fireEvent.click(screen.getByRole('tab', { name: '已有材料' }))
    await waitFor(() =>
      expect(screen.getByTestId('existing-materials-list')).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('checkbox', { name: 'old.md' }))
    fireEvent.click(screen.getByRole('button', { name: '创建运行' }))

    await waitFor(() => expect(mockCreateRun).toHaveBeenCalledOnce())
    expect(mockCreateRun).toHaveBeenCalledWith('ws1', {
      workflow_key: 'demo_workflow',
      items: [{ type: 'material', material_id: 'm-old' }],
    })
    expect(mockCreateSubmitCampaign).not.toHaveBeenCalled()
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
    expect(screen.getByTestId('item-type-hint')).toHaveTextContent('上传文件')
    expect(screen.getByTestId('item-type-hint')).not.toHaveTextContent(
      'accepted_item_types'
    )
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
      '外部平台内容'
    )
  })

  it('drops hidden-panel items when the resolved contract narrows', async () => {
    // 竞态：契约查询未 resolve 时缺省全接受，用户已粘贴 ref id 并完成
    // 上传；契约随后 resolve 为仅 material——隐藏面板残留的 ref 条目
    // 不计数、不提交（此时退回直接创建运行）。
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
    // 残留的 ref 条目不再计数，剩下的上传条目仍可提交（直接创建运行）。
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 1 个条目')
    fireEvent.click(screen.getByRole('button', { name: '创建运行' }))

    await waitFor(() => expect(mockCreateRun).toHaveBeenCalledOnce())
    expect(mockCreateRun).toHaveBeenCalledWith('ws1', {
      workflow_key: 'demo_workflow',
      items: [{ type: 'material', material_id: 'm1' }],
    })
  })

  it('packs a picked folder into one bundle item', async () => {
    const onClose = vi.fn()
    mockRevisionWithAcceptedTypes(['material', 'ref', 'bundle'])
    mockUpload.mockImplementation((_workspaceId, file: File) =>
      Promise.resolve({ materialId: `m-${file.name}`, deduplicated: false })
    )
    mockCreateBundle.mockResolvedValue({ bundle: { id: 'b1' } } as never)
    mockCreateRun.mockResolvedValue({
      run: { id: 'r1' },
      created_count: 1,
    } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={onClose} workspaceId="ws1" />
    )

    const bundleTab = screen.getByRole('tab', { name: '文件夹打包' })
    await waitFor(() => expect(bundleTab).toBeEnabled())
    fireEvent.click(bundleTab)
    pickFiles('add-items-bundle-input', [
      folderFile('root/sub/a.txt', 'a'),
      folderFile('root/b.txt', 'b'),
    ])

    // 成员全部传完后自动创建 manifest：根名剥掉公共前缀，按路径排序。
    await waitFor(() => expect(screen.getByText('就绪')).toBeInTheDocument())
    expect(mockCreateBundle).toHaveBeenCalledWith('ws1', {
      name: 'root',
      members: [
        { material_id: 'm-b.txt', path: 'b.txt' },
        { material_id: 'm-a.txt', path: 'sub/a.txt' },
      ],
    })
    // 一个文件夹只算一个条目。
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 1 个条目')

    fireEvent.click(screen.getByRole('button', { name: '创建运行' }))
    await waitFor(() => expect(mockCreateRun).toHaveBeenCalledOnce())
    expect(mockCreateRun).toHaveBeenCalledWith('ws1', {
      workflow_key: 'demo_workflow',
      items: [{ type: 'bundle', bundle_id: 'b1' }],
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('marks a bundle failed when a member upload fails and retries it', async () => {
    mockRevisionWithAcceptedTypes(['material', 'ref', 'bundle'])
    mockUpload
      .mockRejectedValueOnce(new Error('网络错误'))
      .mockResolvedValue({ materialId: 'm1', deduplicated: false })
    mockCreateBundle.mockResolvedValue({ bundle: { id: 'b1' } } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )

    const bundleTab = screen.getByRole('tab', { name: '文件夹打包' })
    await waitFor(() => expect(bundleTab).toBeEnabled())
    fireEvent.click(bundleTab)
    pickFiles('add-items-bundle-input', [folderFile('root/a.txt', 'a')])

    await waitFor(() =>
      expect(screen.getByText('1 个文件上传失败')).toBeInTheDocument()
    )
    expect(mockCreateBundle).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(screen.getByText('就绪')).toBeInTheDocument())
    expect(mockCreateBundle).toHaveBeenCalledWith('ws1', {
      name: 'root',
      members: [{ material_id: 'm1', path: 'a.txt' }],
    })
  })

  it('retries bundle creation when the create call fails', async () => {
    mockRevisionWithAcceptedTypes(['material', 'ref', 'bundle'])
    mockUpload.mockResolvedValue({ materialId: 'm1', deduplicated: false })
    mockCreateBundle
      .mockRejectedValueOnce(new Error('打包冲突'))
      .mockResolvedValue({ bundle: { id: 'b1' } } as never)
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )

    const bundleTab = screen.getByRole('tab', { name: '文件夹打包' })
    await waitFor(() => expect(bundleTab).toBeEnabled())
    fireEvent.click(bundleTab)
    pickFiles('add-items-bundle-input', [folderFile('root/a.txt', 'a')])

    await waitFor(() =>
      expect(screen.getByText('打包冲突')).toBeInTheDocument()
    )

    // 文件全部成功、创建失败：重试直接重发 create，不重传文件。
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(screen.getByText('就绪')).toBeInTheDocument())
    expect(mockCreateBundle).toHaveBeenCalledTimes(2)
    expect(mockUpload).toHaveBeenCalledTimes(1)
  })

  it('rejects an oversized folder before uploading any member', async () => {
    mockRevisionWithAcceptedTypes(['material', 'ref', 'bundle'])
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )

    const bundleTab = screen.getByRole('tab', { name: '文件夹打包' })
    await waitFor(() => expect(bundleTab).toBeEnabled())
    fireEvent.click(bundleTab)
    const files = Array.from({ length: 1001 }, (_, i) =>
      folderFile(`root/f-${i}.txt`, 'x')
    )
    pickFiles('add-items-bundle-input', files)

    await waitFor(() =>
      expect(screen.getByText(/超过 1000 个成员上限/)).toBeInTheDocument()
    )
    expect(mockUpload).not.toHaveBeenCalled()
    expect(mockCreateBundle).not.toHaveBeenCalled()
  })

  it('enables the bundle tab only when the start node accepts bundles', async () => {
    mockRevisionWithAcceptedTypes(['material', 'ref'])
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '文件夹打包' })).toBeDisabled()
    )
  })

  it('enables the bundle tab when the contract lists bundle', async () => {
    mockRevisionWithAcceptedTypes(['bundle'])
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '文件夹打包' })).toBeEnabled()
    )
    // 其余 tab 被禁用后落到 bundle tab。
    expect(screen.getByTestId('add-items-bundle-input')).toBeInTheDocument()
  })
})
