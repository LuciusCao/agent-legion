import { beforeEach, describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { AddDialog } from './AddDialog'
import { api, fetchWorkflowDefinition } from '../api'
import { useUiStore } from '../stores/uiStore'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchWorkflowDefinition: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockFetchWorkflowDefinition = vi.mocked(fetchWorkflowDefinition)

function enterResourceIds(value: string) {
  const input = document.querySelector(
    'md-outlined-text-field[type="textarea"]'
  ) as HTMLInputElement
  input.value = value
  fireEvent.input(input)
}

describe('AddDialog', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockFetchWorkflowDefinition.mockReset()
    useUiStore.setState({ addContentType: 'knowledge', toast: null })
  })

  it('renders dialog with correct title', () => {
    render(<AddDialog open={true} onClose={vi.fn()} />)
    expect(screen.getByText('添加资源')).toBeInTheDocument()
  })

  it('disables submit button when input is empty and enables after typing', () => {
    render(<AddDialog open={true} onClose={vi.fn()} context="video" />)

    const button = screen
      .getByText('加入队列')
      .closest('md-filled-button') as HTMLElement
    expect(button).toHaveAttribute('disabled')

    enterResourceIds('x11090605')

    expect(button).not.toHaveAttribute('disabled')
  })

  it('switches the video content type and updates the input label', () => {
    render(<AddDialog open={true} onClose={vi.fn()} context="video" />)

    fireEvent.click(screen.getByText('题目'))

    expect(useUiStore.getState().addContentType).toBe('question')
    expect(
      document.querySelector('md-outlined-text-field[label="题目 ID"]')
    ).toBeTruthy()
  })

  it('submits normalized video resource inputs', async () => {
    mockApi.mockResolvedValue({ videos: [], results: [] })
    render(<AddDialog open={true} onClose={vi.fn()} context="video" />)
    enterResourceIds('x11090605, uuid-1\nx11090606')

    fireEvent.click(screen.getByText('加入队列'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/videos', {
        method: 'POST',
        body: JSON.stringify({
          items: [
            {
              content_type: 'knowledge',
              external_id: 'x11090605',
              source_uuid: 'uuid-1',
            },
            {
              content_type: 'knowledge',
              external_id: 'x11090606',
              source_uuid: '',
            },
          ],
        }),
      })
    })
  })

  it('shows error toast when video intake fails', async () => {
    mockApi.mockRejectedValue(new Error('Intake failed'))
    render(<AddDialog open={true} onClose={vi.fn()} context="video" />)
    enterResourceIds('x11090605')

    fireEvent.click(screen.getByText('加入队列'))

    await waitFor(() => {
      expect(useUiStore.getState().toast).toEqual(
        expect.objectContaining({ type: 'error' })
      )
    })
  })

  it('shows error toast when workspace job batch creation fails', async () => {
    mockFetchWorkflowDefinition.mockResolvedValue({
      workflow: {
        key: 'question_comprehension_info',
        label: '题目审题信息生成 DAG',
        intake: {
          modes: [
            {
              key: 'batch_by_ids',
              label: '按题目ID批量',
              input_field: 'question_ids',
              resource: '',
            },
          ],
        },
        nodes: [],
      },
    })
    mockApi
      .mockResolvedValueOnce({
        workspace: {
          id: 'ws1',
          name: '题目审题信息',
          default_workflow_key: 'question_comprehension_info',
          default_entity: 'question',
        },
      })
      .mockRejectedValueOnce(new Error('Backend failure'))

    render(
      <AddDialog
        open={true}
        onClose={vi.fn()}
        context="workspace"
        workspaceId="ws1"
      />
    )

    await waitFor(() => {
      expect(document.querySelector('md-select-option')).toBeInTheDocument()
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
        key: 'question_comprehension_info',
        label: '题目审题信息生成 DAG',
        intake: {
          modes: [
            {
              key: 'batch_by_knowledge',
              label: '按知识点批量',
              input_field: 'knowledge_codes',
              resource: '',
            },
            {
              key: 'batch_by_ids',
              label: '按题目ID批量',
              input_field: 'question_ids',
              resource: '',
            },
          ],
        },
        nodes: [],
      },
    })
    mockApi
      .mockResolvedValueOnce({
        workspace: {
          id: 'ws1',
          name: '题目审题信息',
          default_workflow_key: 'question_comprehension_info',
          default_entity: 'question',
          intake_config: {
            enabled_modes: ['batch_by_knowledge', 'batch_by_ids'],
            label_overrides: {},
          },
        },
      })
      .mockResolvedValueOnce({ batch: {}, created_count: 1, jobs: [] })

    render(
      <AddDialog
        open={true}
        onClose={vi.fn()}
        context="workspace"
        workspaceId="ws1"
      />
    )

    await waitFor(() => {
      expect(document.querySelector('md-select-option')).toBeInTheDocument()
    })

    const select = document.querySelector('md-outlined-select') as HTMLElement
    await act(async () => {
      select.dispatchEvent(
        new CustomEvent('change', {
          detail: { value: 'batch_by_ids' },
          bubbles: true,
        })
      )
    })

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
      workflow_key: 'question_comprehension_info',
      entity: 'question',
      source_kind: 'batch_by_ids',
      question_ids: ['5cf31cfe5805488fe22aea87b3853267'],
      knowledge_codes: [],
    })
  })

  it('disables submit and shows hint when workspace enabled_modes is empty', async () => {
    mockFetchWorkflowDefinition.mockResolvedValue({
      workflow: {
        key: 'question_comprehension_info',
        label: '题目审题信息生成 DAG',
        intake: {
          modes: [
            {
              key: 'batch_by_ids',
              label: '按题目ID批量',
              input_field: 'question_ids',
              resource: '',
            },
          ],
        },
        nodes: [],
      },
    })
    mockApi.mockResolvedValueOnce({
      workspace: {
        id: 'ws1',
        name: '题目审题信息',
        default_workflow_key: 'question_comprehension_info',
        default_entity: 'question',
        intake_config: { enabled_modes: [], label_overrides: {} },
      },
    })

    render(
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

    const button = screen
      .getByText('加入队列')
      .closest('md-filled-button') as HTMLElement
    expect(button).toHaveAttribute('disabled')
  })
})
