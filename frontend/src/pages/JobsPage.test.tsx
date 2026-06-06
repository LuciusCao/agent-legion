import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { JobsPage } from './JobsPage'

describe('JobsPage', () => {
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

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <JobsPage />
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
    expect(screen.getByText('queued')).toBeInTheDocument()
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
        <JobsPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('题目工厂未启用')).toBeInTheDocument()
    })
  })

  it('creates a workspace and navigates to it', async () => {
    vi.spyOn(globalThis, 'fetch')
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

    render(
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/workspaces']}
      >
        <Routes>
          <Route path="/workspaces" element={<JobsPage />} />
          <Route path="/workspaces/:workspaceId" element={<JobsPage />} />
        </Routes>
      </MemoryRouter>
    )

    fireEvent.change(await screen.findByLabelText('新建工作空间名称'), {
      target: { value: 'Math Sprint' },
    })
    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Math Sprint' })).toBeInTheDocument()
    })
  })
})
