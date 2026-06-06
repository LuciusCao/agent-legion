import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
        <JobsPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('题目工厂未启用')).toBeInTheDocument()
    })
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

    // Open create workspace dialog
    fireEvent.click(await screen.findByText('新建工作空间'))

    // Fill in the name field inside the dialog
    const nameField = await screen.findByLabelText('名称')
    await act(async () => {
      ;(nameField as HTMLInputElement).value = 'Math Sprint'
      nameField.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    // Click create button (second md-outlined-button in the dialog)
    const outlinedButtons = document.querySelectorAll('md-outlined-button')
    expect(outlinedButtons.length).toBeGreaterThanOrEqual(1)
    // The create button is inside the dialog; find it by checking which one is in md-dialog
    const createBtn = Array.from(outlinedButtons).find((btn) =>
      btn.closest('md-dialog')
    )
    expect(createBtn).toBeDefined()
    fireEvent.click(createBtn!)

    // Verify API was called (handleCreateWorkspace was invoked)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3)
    })

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Math Sprint' })).toBeInTheDocument()
    })
  })
})
