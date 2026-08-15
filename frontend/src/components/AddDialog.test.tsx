import { beforeEach, describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AddDialog } from './AddDialog'
import { api, fetchWorkflowDefinition } from '../api'
import { useUiStore } from '../stores/uiStore'
import { TestQueryProvider } from '../testing/testQueryClient'
import type { ReactElement } from 'react'

function renderWithClient(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchWorkflowDefinition: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockFetchWorkflowDefinition = vi.mocked(fetchWorkflowDefinition)

function enterResourceIds(value: string) {
  const input = screen.getByRole('textbox') as HTMLInputElement
  fireEvent.change(input, { target: { value } })
}

function selectMode(label: string) {
  const combobox = screen.getByLabelText('导入模式')
  fireEvent.mouseDown(combobox)
  const option = screen.getByRole('option', { name: label })
  fireEvent.click(option)
}

describe('AddDialog', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockFetchWorkflowDefinition.mockReset()
    useUiStore.setState({ addContentType: 'knowledge', toast: null })
  })

  it('renders dialog with correct title', () => {
    renderWithClient(<AddDialog open={true} onClose={vi.fn()} />)
    expect(screen.getByText('添加资源')).toBeInTheDocument()
  })

  it('disables submit button when input is empty and enables after typing', () => {
    renderWithClient(
      <AddDialog open={true} onClose={vi.fn()} context="video" />
    )

    const button = screen.getByRole('button', { name: '加入队列' })
    expect(button).toBeDisabled()

    enterResourceIds('x11090605')

    expect(button).not.toBeDisabled()
  })

  it('switches the video content type and updates the input label', () => {
    renderWithClient(
      <AddDialog open={true} onClose={vi.fn()} context="video" />
    )

    fireEvent.click(screen.getByText('题目'))

    expect(useUiStore.getState().addContentType).toBe('question')
    expect(screen.getByLabelText('题目 ID')).toBeInTheDocument()
  })

  it('shows error toast when workspace job batch creation fails', async () => {
    mockFetchWorkflowDefinition.mockResolvedValue({
      workflow: {
        key: 'demo_workflow',
        label: '题目审题信息生成 DAG',
        intake: {
          modes: [
            {
              key: 'batch_by_ids',
              label: '按题目ID批量',
              input_field: 'question_ids',
            },
          ],
        },
        edges: [],
        nodes: [],
      },
    })
    mockApi
      .mockResolvedValueOnce({
        workspace: {
          id: 'ws1',
          name: '题目审题信息',
          default_workflow_key: 'demo_workflow',
          default_entity: 'question',
        },
      })
      .mockRejectedValueOnce(new Error('Backend failure'))

    renderWithClient(
      <AddDialog
        open={true}
        onClose={vi.fn()}
        context="workspace"
        workspaceId="ws1"
      />
    )

    await waitFor(() => {
      expect(screen.getByLabelText('导入模式')).toBeInTheDocument()
    })
    // 等工作流定义到达（模式选项就绪）后再操作。
    await waitFor(() => {
      expect(screen.getAllByText('按题目ID批量').length).toBeGreaterThan(0)
    })

    enterResourceIds('5cf31cfe5805488fe22aea87b3853267')
    fireEvent.click(screen.getByText('加入队列'))

    await waitFor(() => {
      expect(useUiStore.getState().toast).toEqual(
        expect.objectContaining({ type: 'error' })
      )
    })
  })

  it('submits with the selected intake mode when multiple modes exist', async () => {
    mockFetchWorkflowDefinition.mockResolvedValue({
      workflow: {
        key: 'demo_workflow',
        label: '题目审题信息生成 DAG',
        intake: {
          modes: [
            {
              key: 'batch_by_knowledge',
              label: '按知识点批量',
              input_field: 'knowledge_codes',
            },
            {
              key: 'batch_by_ids',
              label: '按题目ID批量',
              input_field: 'question_ids',
            },
          ],
        },
        edges: [],
        nodes: [],
      },
    })
    mockApi
      .mockResolvedValueOnce({
        workspace: {
          id: 'ws1',
          name: '题目审题信息',
          default_workflow_key: 'demo_workflow',
          default_entity: 'question',
          intake_config: {
            enabled_modes: ['batch_by_knowledge', 'batch_by_ids'],
            label_overrides: {},
          },
        },
      })
      .mockResolvedValueOnce({
        batch: { status: 'queued' },
        created_count: 0,
        jobs: [],
      })

    renderWithClient(
      <AddDialog
        open={true}
        onClose={vi.fn()}
        context="workspace"
        workspaceId="ws1"
      />
    )

    await waitFor(() => {
      expect(screen.getByLabelText('导入模式')).toBeInTheDocument()
    })
    // 等工作流定义到达（模式选项就绪）后再操作。
    await waitFor(() => {
      expect(screen.getAllByText('按知识点批量').length).toBeGreaterThan(0)
    })

    selectMode('按题目ID批量')

    enterResourceIds('5cf31cfe5805488fe22aea87b3853267')
    fireEvent.click(screen.getByText('加入队列'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenLastCalledWith(
        '/api/workspaces/ws1/job-batches',
        expect.objectContaining({
          method: 'POST',
        })
      )
    })
    const body = JSON.parse(
      mockApi.mock.calls[mockApi.mock.calls.length - 1][1]?.body as string
    )
    expect(body).toMatchObject({
      workflow_key: 'demo_workflow',
      entity: 'question',
      source_kind: 'batch_by_ids',
      question_ids: ['5cf31cfe5805488fe22aea87b3853267'],
      knowledge_codes: [],
      async_processing: true,
    })
    expect(useUiStore.getState().toast).toEqual(
      expect.objectContaining({
        type: 'success',
        message: '已加入队列，共 1 项',
      })
    )
  })

  it('attaches the select change listener when the dialog opens after mount', async () => {
    mockFetchWorkflowDefinition.mockResolvedValue({
      workflow: {
        key: 'demo_workflow',
        label: '题目审题信息生成 DAG',
        intake: {
          modes: [
            {
              key: 'batch_by_knowledge',
              label: '按知识点批量',
              input_field: 'knowledge_codes',
            },
            {
              key: 'batch_by_ids',
              label: '按题目ID批量',
              input_field: 'question_ids',
            },
          ],
        },
        edges: [],
        nodes: [],
      },
    })
    mockApi
      .mockResolvedValueOnce({
        workspace: {
          id: 'ws1',
          name: '题目审题信息',
          default_workflow_key: 'demo_workflow',
          default_entity: 'question',
          intake_config: {
            enabled_modes: ['batch_by_knowledge', 'batch_by_ids'],
            label_overrides: {},
          },
        },
      })
      .mockResolvedValueOnce({
        batch: { status: 'queued' },
        created_count: 0,
        jobs: [],
      })

    const { rerender } = renderWithClient(
      <AddDialog
        open={false}
        onClose={vi.fn()}
        context="workspace"
        workspaceId="ws1"
      />
    )

    rerender(
      <TestQueryProvider>
        <AddDialog
          open={true}
          onClose={vi.fn()}
          context="workspace"
          workspaceId="ws1"
        />
      </TestQueryProvider>
    )

    await waitFor(() => {
      expect(screen.getByLabelText('导入模式')).toBeInTheDocument()
    })
    // 等工作流定义到达（模式选项就绪）后再操作。
    await waitFor(() => {
      expect(screen.getAllByText('按知识点批量').length).toBeGreaterThan(0)
    })

    selectMode('按题目ID批量')

    enterResourceIds('5cf31cfe5805488fe22aea87b3853267')
    fireEvent.click(screen.getByText('加入队列'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenLastCalledWith(
        '/api/workspaces/ws1/job-batches',
        expect.objectContaining({
          method: 'POST',
        })
      )
    })
    const body = JSON.parse(
      mockApi.mock.calls[mockApi.mock.calls.length - 1][1]?.body as string
    )
    expect(body).toMatchObject({
      source_kind: 'batch_by_ids',
      question_ids: ['5cf31cfe5805488fe22aea87b3853267'],
      knowledge_codes: [],
      async_processing: true,
    })
  })

  it('disables submit and shows hint when workspace enabled_modes is empty', async () => {
    mockFetchWorkflowDefinition.mockResolvedValue({
      workflow: {
        key: 'demo_workflow',
        label: '题目审题信息生成 DAG',
        intake: {
          modes: [
            {
              key: 'batch_by_ids',
              label: '按题目ID批量',
              input_field: 'question_ids',
            },
          ],
        },
        edges: [],
        nodes: [],
      },
    })
    mockApi.mockResolvedValueOnce({
      workspace: {
        id: 'ws1',
        name: '题目审题信息',
        default_workflow_key: 'demo_workflow',
        default_entity: 'question',
        intake_config: { enabled_modes: [], label_overrides: {} },
      },
    })

    renderWithClient(
      <AddDialog
        open={true}
        onClose={vi.fn()}
        context="workspace"
        workspaceId="ws1"
      />
    )

    await waitFor(() => {
      expect(screen.getByText(/未启用任何接入模式/)).toBeInTheDocument()
    })

    enterResourceIds('5cf31cfe5805488fe22aea87b3853267')

    const button = screen.getByRole('button', { name: '加入队列' })
    expect(button).toBeDisabled()
  })
})
