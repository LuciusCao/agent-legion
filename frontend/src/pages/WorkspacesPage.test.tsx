import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { WorkspacesPage } from './WorkspacesPage'

describe('WorkspacesPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders jobs from the neutral jobs api', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          workspaces: [
            {
              id: 'default',
              name: '默认工作空间',
              default_pipeline_key: 'question_content',
            },
          ],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          jobs: [
            {
              id: 'default_question_content_Q001',
              workspace_id: 'default',
              pipeline_key: 'question_content',
              source_id: 'Q001',
              title: 'Question Q001',
              status: 'queued',
            },
          ],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          pipeline: {
            key: 'question_content',
            label: '题目内容生成',
            concurrency: { local: 8, agent: 2 },
            nodes: [],
          },
        }),
      } as Response)

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkspacesPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Question Q001')).toBeInTheDocument()
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/workspaces', expect.any(Object))
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/default/jobs?pipeline_key=question_content',
      expect.any(Object)
    )
    expect(screen.getByText('Q001')).toBeInTheDocument()
    expect(screen.getByText('排队中')).toBeInTheDocument()
  })

  it('shows disabled message when api returns 404', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: false,
        status: 404,
        json: async () => ({ detail: 'Pipelines are disabled' }),
        text: async () => JSON.stringify({ detail: 'Pipelines are disabled' }),
      } as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 404,
        json: async () => ({ detail: 'Pipelines are disabled' }),
        text: async () => JSON.stringify({ detail: 'Pipelines are disabled' }),
      } as Response)

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkspacesPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('题目工厂未启用')).toBeInTheDocument()
    })
  })

  it('creates question production jobs from pasted question ids', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          workspaces: [
            { id: 'default', name: '默认工作空间', default_pipeline_key: 'question_content' },
          ],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ jobs: [] }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          pipeline: {
            key: 'question_content',
            label: '题目内容生成',
            concurrency: { local: 8, agent: 2 },
            nodes: [
              { key: 'fetch_question_context', runner: 'local', after: [], inputs: [], outputs: ['question_context.json'] },
              { key: 'assemble_package', runner: 'local', after: ['fetch_question_context'], inputs: ['question_context.json'], outputs: ['upload_params.json'] },
            ],
          },
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          batch: { id: 'default_question_content_question_ids_abc' },
          created_count: 2,
          jobs: [
            {
              id: 'default_question_content_Q001',
              workspace_id: 'default',
              pipeline_key: 'question_content',
              source_id: 'Q001',
              title: 'Question Q001',
              status: 'queued',
            },
            {
              id: 'default_question_content_Q002',
              workspace_id: 'default',
              pipeline_key: 'question_content',
              source_id: 'Q002',
              title: 'Question Q002',
              status: 'queued',
            },
          ],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          jobs: [
            {
              id: 'default_question_content_Q001',
              workspace_id: 'default',
              pipeline_key: 'question_content',
              source_id: 'Q001',
              title: 'Question Q001',
              status: 'queued',
            },
            {
              id: 'default_question_content_Q002',
              workspace_id: 'default',
              pipeline_key: 'question_content',
              source_id: 'Q002',
              title: 'Question Q002',
              status: 'queued',
            },
          ],
        }),
      } as Response)

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkspacesPage />
      </MemoryRouter>
    )

    const input = await screen.findByLabelText('题目 ID')
    await act(async () => {
      ;(input as HTMLTextAreaElement).value = 'Q001\nQ002\nQ001'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })
    fireEvent.click(screen.getByText('创建生产任务'))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/workspaces/default/job-batches',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            pipeline_key: 'question_content',
            source_kind: 'question_ids',
            question_ids: ['Q001', 'Q002'],
            knowledge_codes: [],
          }),
        })
      )
    })
    expect(await screen.findByText('Question Q001')).toBeInTheDocument()
    expect(screen.getByText('Question Q002')).toBeInTheDocument()
  })

  it('does not submit a batch when question ids are empty', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          workspaces: [
            { id: 'default', name: '默认工作空间', default_pipeline_key: 'question_content' },
          ],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ jobs: [] }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          pipeline: {
            key: 'question_content',
            label: '题目内容生成',
            concurrency: { local: 8, agent: 2 },
            nodes: [],
          },
        }),
      } as Response)

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkspacesPage />
      </MemoryRouter>
    )

    const button = await screen.findByText('创建生产任务')
    fireEvent.click(button)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3)
    })
    expect(screen.getByText('请先输入题目 ID')).toBeInTheDocument()
  })

  it('creates a workspace and navigates to it', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          workspaces: [
            {
              id: 'default',
              name: '默认工作空间',
              default_pipeline_key: 'question_content',
            },
          ],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ jobs: [] }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          pipeline: {
            key: 'question_content',
            label: '题目内容生成',
            concurrency: { local: 8, agent: 2 },
            nodes: [],
          },
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          workspace: {
            id: 'math_sprint',
            name: 'Math Sprint',
            default_pipeline_key: 'question_content',
          },
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          workspaces: [
            {
              id: 'default',
              name: '默认工作空间',
              default_pipeline_key: 'question_content',
            },
            {
              id: 'math_sprint',
              name: 'Math Sprint',
              default_pipeline_key: 'question_content',
            },
          ],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ jobs: [] }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          pipeline: {
            key: 'question_content',
            label: '题目内容生成',
            concurrency: { local: 8, agent: 2 },
            nodes: [],
          },
        }),
      } as Response)

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/workspaces']}
      >
        <Routes>
          <Route path="/workspaces" element={<WorkspacesPage />} />
          <Route path="/workspaces/:workspaceId" element={<WorkspacesPage />} />
        </Routes>
      </MemoryRouter>
    )

    // Open create workspace dialog
    fireEvent.click(await screen.findByText('新建工作空间'))

    // Fill in the name field inside the dialog
    const nameField = await screen.findByLabelText('名称')
    await act(async () => {
      ;(nameField as HTMLInputElement).value = 'Math Sprint'
      nameField.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    // Click create button inside the dialog
    const createBtn = screen.getByText('创建')
    fireEvent.click(createBtn)

    // Verify API was called (handleCreateWorkspace was invoked)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(4)
    })

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Math Sprint' })).toBeInTheDocument()
    })
  })
})
