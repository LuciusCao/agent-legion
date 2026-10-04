import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'

import { AddItemsDialog } from './AddItemsDialog'
import { api, fetchActiveWorkflowRevision } from '../api'
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
const mockUpload = vi.mocked(uploadMaterialFile)
const mockFetchRevision = vi.mocked(fetchActiveWorkflowRevision)

function renderWithClient(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

function mockWorkspace() {
  mockApi.mockImplementation(
    (path: unknown) =>
      Promise.resolve(
        String(path).includes('/materials')
          ? ({
              materials: [],
              total: 0,
              limit: 0,
              offset: 0,
            } as MaterialListResponse)
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

function pickFiles(testId: string, files: File[]) {
  fireEvent.change(screen.getByTestId(testId), { target: { files } })
}

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
          text_input: null,
          after: [],
          inputs: [],
          outputs: [],
        },
      ],
      edges: [],
    },
  } as never)
}

// text 条目文件名的前端同契约校验（#761 codex 列车 P2 + #911 P1）：
// 无效名提交前拦截、混合提交不得静默丢弃已改动内容。
describe('AddItemsDialog text filename validation', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockUpload.mockReset()
    mockFetchRevision.mockReset()
    useUiStore.setState({ toast: null })
    mockWorkspace()
  })

  it('blocks submit with an invalid filename before the backend rejects it', async () => {
    mockRevisionWithAcceptedTypes(['text'])
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    const textTab = screen.getByRole('tab', { name: '输入需求' })
    await waitFor(() => expect(textTab).toBeEnabled())
    fireEvent.click(textTab)

    fireEvent.change(screen.getByLabelText('需求内容'), {
      target: { value: '实际需求' },
    })
    fireEvent.change(screen.getByLabelText('文件名'), {
      target: { value: '../notes.md' },
    })
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 0 个条目')
    expect(screen.getByRole('button', { name: '创建运行' })).toBeDisabled()
    expect(screen.getByTestId('text-summary')).toHaveTextContent(
      '文件名不能含路径分隔符'
    )

    fireEvent.change(screen.getByLabelText('文件名'), {
      target: { value: 'notes.pdf' },
    })
    expect(screen.getByTestId('text-summary')).toHaveTextContent(
      '文件名须以 .md 或 .txt 结尾'
    )
    expect(screen.getByRole('button', { name: '创建运行' })).toBeDisabled()

    fireEvent.change(screen.getByLabelText('文件名'), {
      target: { value: 'notes.md' },
    })
    expect(screen.getByTestId('total-count')).toHaveTextContent('共 1 个条目')
    expect(screen.getByRole('button', { name: '创建运行' })).toBeEnabled()
  })

  it('blocks mixed submission while a touched text item has an invalid filename', async () => {
    mockRevisionWithAcceptedTypes(['material', 'text'])
    mockUpload.mockResolvedValue({ materialId: 'm1', deduplicated: false })
    renderWithClient(
      <AddItemsDialog open={true} onClose={vi.fn()} workspaceId="ws1" />
    )
    pickFiles('add-items-file-input', [new File(['a'], 'a.txt')])
    await waitFor(() =>
      expect(screen.getByTestId('total-count')).toHaveTextContent('共 1 个条目')
    )
    const submit = screen.getByRole('button', { name: '创建运行' })
    expect(submit).toBeEnabled()

    // 输入了内容但文件名无效：text 条目不计数，且不得被静默丢弃——
    // 混合提交整体阻塞并给出提示。
    fireEvent.click(screen.getByRole('tab', { name: '输入需求' }))
    fireEvent.change(screen.getByLabelText('需求内容'), {
      target: { value: '实际需求' },
    })
    fireEvent.change(screen.getByLabelText('文件名'), {
      target: { value: 'notes.pdf' },
    })
    expect(screen.getByRole('button', { name: '创建运行' })).toBeDisabled()
    expect(screen.getByTestId('text-filename-hint')).toBeInTheDocument()

    // 修正文件名恢复可提交；清空内容（不再是条目）同样恢复。
    fireEvent.change(screen.getByLabelText('文件名'), {
      target: { value: 'notes.md' },
    })
    expect(screen.getByRole('button', { name: '创建运行' })).toBeEnabled()
    fireEvent.change(screen.getByLabelText('文件名'), {
      target: { value: 'notes.pdf' },
    })
    fireEvent.change(screen.getByLabelText('需求内容'), {
      target: { value: '   ' },
    })
    expect(screen.getByRole('button', { name: '创建运行' })).toBeEnabled()
  })
})
