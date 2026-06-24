import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { JobReviewPanel } from './JobReviewPanel'

const mockFetchJobArtifact = vi.fn()

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return {
    ...mod,
    fetchJobArtifact: (...args: unknown[]) => mockFetchJobArtifact(...args),
  }
})

describe('JobReviewPanel', () => {
  beforeEach(() => {
    mockFetchJobArtifact.mockReset()
  })

  it('renders nothing when there are no review artifacts', () => {
    const { container } = render(
      <JobReviewPanel jobId="j1" artifacts={['questions.json']} />
    )
    expect(container.firstChild).toBeNull()
    expect(mockFetchJobArtifact).not.toHaveBeenCalled()
  })

  it('renders loading state and then report summaries and decisions', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({
        question_id: 'q1',
        approved_count: 1,
        rejected_count: 1,
        warnings: ['warn'],
        decisions: [
          { key_info_id: 'ki_1', decision: 'approved', reason: 'good' },
          { key_info_id: 'ki_2', decision: 'rejected', reason: 'bad' },
        ],
      }),
    })
    render(
      <JobReviewPanel jobId="j1" artifacts={['key_info_review_report.json']} />
    )
    expect(
      screen.getByText(/加载 key_info_review_report.json/)
    ).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('审核关键信息')).toBeInTheDocument()
    })
    expect(screen.getByText('ki_1')).toBeInTheDocument()
    expect(screen.getByText('ki_2')).toBeInTheDocument()
    expect(screen.getByText('warn')).toBeInTheDocument()
    expect(screen.getByText('good')).toBeInTheDocument()
  })

  it('renders multiple review reports', async () => {
    mockFetchJobArtifact.mockImplementation((_jobId, name) => {
      if (name === 'key_info_review_report.json') {
        return Promise.resolve({
          content: JSON.stringify({
            question_id: 'q1',
            approved_count: 1,
            rejected_count: 0,
            warnings: [],
            decisions: [{ key_info_id: 'ki_1', decision: 'approved' }],
          }),
        })
      }
      if (name === 'review_result.json') {
        return Promise.resolve({
          content: JSON.stringify({
            review_status: 'rejected',
            review_msg: 'fix it',
            details: [{ item: 'x', result: 'rejected', reason: 'wrong' }],
          }),
        })
      }
      return Promise.reject(new Error('unknown'))
    })
    render(
      <JobReviewPanel
        jobId="j1"
        artifacts={[
          'key_info_review_report.json',
          'review_result.json',
          'questions.json',
        ]}
      />
    )
    await waitFor(() => {
      expect(screen.getByText('审核关键信息')).toBeInTheDocument()
      expect(screen.getByText('内容审核')).toBeInTheDocument()
    })
    expect(screen.queryByText('questions.json')).not.toBeInTheDocument()
    expect(screen.getByText('x')).toBeInTheDocument()
    expect(screen.getByText('wrong')).toBeInTheDocument()
  })

  it('shows error message when fetching an artifact fails', async () => {
    mockFetchJobArtifact.mockRejectedValue(new Error('fetch failed'))
    render(
      <JobReviewPanel jobId="j1" artifacts={['key_info_review_report.json']} />
    )
    await waitFor(() => {
      expect(screen.getByText('fetch failed')).toBeInTheDocument()
    })
  })

  it('renders needs_revision decision with distinct badge', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({
        review_status: 'needs_revision',
        review_msg: 'please revise',
        details: [
          { item: 'x', result: 'needs_revision', reason: 'unclear wording' },
        ],
      }),
    })
    render(<JobReviewPanel jobId="j1" artifacts={['review_result.json']} />)
    await waitFor(() => {
      expect(screen.getByText('内容审核')).toBeInTheDocument()
    })
    expect(screen.getByText('x')).toBeInTheDocument()
    expect(screen.getByText('unclear wording')).toBeInTheDocument()
    expect(screen.getByTestId('BuildCircleIcon')).toBeInTheDocument()
  })

  it('renders raw JSON fallback when review_result has no known fields', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({
        status: 'pending',
        message: 'not yet finalized',
      }),
    })
    render(<JobReviewPanel jobId="j1" artifacts={['review_result.json']} />)
    await waitFor(() => {
      expect(screen.getByText('内容审核')).toBeInTheDocument()
    })
    expect(screen.getByText(/"status": "pending"/)).toBeInTheDocument()
    expect(
      screen.getByText(/"message": "not yet finalized"/)
    ).toBeInTheDocument()
  })

  it('shows warnings count in summary', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({
        question_id: 'q1',
        approved_count: 1,
        rejected_count: 0,
        warnings: ['warning one', 'warning two'],
        decisions: [{ key_info_id: 'ki_1', decision: 'approved' }],
      }),
    })
    render(
      <JobReviewPanel jobId="j1" artifacts={['key_info_review_report.json']} />
    )
    await waitFor(() => {
      expect(screen.getByText('审核关键信息')).toBeInTheDocument()
    })
    expect(screen.getByTitle('警告: 2')).toBeInTheDocument()
    expect(screen.getByText('warning one')).toBeInTheDocument()
    expect(screen.getByText('warning two')).toBeInTheDocument()
  })

  it('refetches when refreshKey changes', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      content: JSON.stringify({
        question_id: 'q1',
        approved_count: 0,
        rejected_count: 0,
        warnings: [],
        decisions: [],
      }),
    })
    const { rerender } = render(
      <JobReviewPanel
        jobId="j1"
        artifacts={['key_info_review_report.json']}
        refreshKey="a"
      />
    )
    await waitFor(() => {
      expect(mockFetchJobArtifact).toHaveBeenCalledTimes(1)
    })
    rerender(
      <JobReviewPanel
        jobId="j1"
        artifacts={['key_info_review_report.json']}
        refreshKey="b"
      />
    )
    await waitFor(() => {
      expect(mockFetchJobArtifact).toHaveBeenCalledTimes(2)
    })
  })
})
