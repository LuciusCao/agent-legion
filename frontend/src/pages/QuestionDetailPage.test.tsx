import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import QuestionDetailPage from './QuestionDetailPage'

const mockDetail = {
  question_id: 'Q100',
  title: 'Algebra Problem',
  normalized: {
    stem: 'What is 2+2?',
    options: [
      { label: 'A', content: '3' },
      { label: 'B', content: '4' },
    ],
    answer: ['B'],
    analysis: 'Basic arithmetic.',
  },
  cms_payload: { code: 0 },
  jobs: [
    {
      id: 'j1',
      workspace_id: 'ws1',
      pipeline_key: 'question_content',
      source_id: 'Q100',
      title: 'Algebra Problem',
      status: 'completed',
      completed_nodes: 5,
      total_nodes: 5,
      created_at: '2026-06-09T08:00:00Z',
    },
  ],
}

function renderPage(initialEntry = '/workspaces/ws1/questions/Q100') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/questions/:questionId"
          element={<QuestionDetailPage />}
        />
        <Route
          path="/workspaces/:workspaceId/jobs/:jobId"
          element={<div data-testid="job-detail-page">Job Detail</div>}
        />
        <Route
          path="/workspaces/:workspaceId"
          element={<div data-testid="workspace-page">Workspace</div>}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('QuestionDetailPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders question stem and options', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockDetail,
      })
    )
    renderPage()
    expect(await screen.findByText('What is 2+2?')).toBeInTheDocument()
    expect(screen.getByText('A.')).toBeInTheDocument()
    expect(screen.getByText('B.')).toBeInTheDocument()
  })

  it('highlights correct option', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockDetail,
      })
    )
    const { container } = renderPage()
    await waitFor(() => screen.getByText('What is 2+2?'))
    const correct = container.querySelector('[class*="_correct_"]')
    expect(correct).toBeTruthy()
    expect(correct?.textContent).toContain('B.')
  })

  it('navigates to job detail when clicking a job', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockDetail,
      })
    )
    renderPage()
    const titles = await screen.findAllByText('Algebra Problem')
    fireEvent.click(titles[titles.length - 1])
    expect(await screen.findByTestId('job-detail-page')).toBeInTheDocument()
  })
})
