import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { JobsPage } from './JobsPage'

describe('JobsPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders jobs from the neutral jobs api', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        jobs: [
          {
            id: 'question_content_Q001',
            pipeline_key: 'question_content',
            source_id: 'Q001',
            title: 'Question Q001',
            status: 'queued',
          },
        ],
      }),
    } as Response)

    render(<JobsPage />)

    await waitFor(() => {
      expect(screen.getByText('Question Q001')).toBeInTheDocument()
    })
    expect(screen.getByText('Q001')).toBeInTheDocument()
    expect(screen.getByText('queued')).toBeInTheDocument()
  })

  it('shows disabled message when api returns 404', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({ detail: 'Pipelines are disabled' }),
      text: async () => JSON.stringify({ detail: 'Pipelines are disabled' }),
    } as Response)

    render(<JobsPage />)

    await waitFor(() => {
      expect(screen.getByText('题目工厂未启用')).toBeInTheDocument()
    })
  })
})
