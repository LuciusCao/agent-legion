import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QuestionContentPanel } from './QuestionContentPanel'

const mockFetchJobArtifact = vi.fn()

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return {
    ...mod,
    fetchJobArtifact: (...args: unknown[]) => mockFetchJobArtifact(...args),
  }
})

function makeQuestionsJson(normalized: Record<string, unknown>) {
  return {
    content: JSON.stringify({
      questions: [
        {
          question_id: 'Q1',
          normalized,
        },
      ],
    }),
  }
}

describe('QuestionContentPanel', () => {
  beforeEach(() => {
    mockFetchJobArtifact.mockReset()
  })

  it('renders answer badges for array answer', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>What is 1+1?</p>',
        options: [
          { label: 'A', content: '1' },
          { label: 'B', content: '2' },
        ],
        answer: ['B'],
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(screen.getByText('B')).toBeInTheDocument()
  })

  it('renders answer badge with LaTeX', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>What is x?</p>',
        answer: ['$x = 2$'],
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(document.querySelector('.katex')).toBeInTheDocument()
  })

  it('shows check icon for correct option', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>Pick one</p>',
        options: [
          { label: 'A', content: 'Wrong' },
          { label: 'B', content: 'Right' },
        ],
        answer: ['B'],
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('选项')).toBeInTheDocument())
    const listItems = screen.getAllByRole('listitem')
    expect(listItems).toHaveLength(2)
    expect(listItems[1].querySelector('md-icon')).toHaveTextContent('check')
  })

  it('falls back to raw data for complex answer', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>Complex</p>',
        answer: { nested: { value: 'deep' } },
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(screen.getByText('无答案')).toBeInTheDocument()
  })

  it('marks correct option for single-string answer', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>Pick one</p>',
        options: [
          { label: 'A', content: 'Wrong' },
          { label: 'B', content: 'Right' },
        ],
        answer: 'B',
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('选项')).toBeInTheDocument())
    const listItems = screen.getAllByRole('listitem')
    expect(listItems[1].querySelector('md-icon')).toHaveTextContent('check')
  })

  it('renders structured answer blanks with alternatives', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>Fill blanks</p>',
        answer_blanks: [
          { alternatives: ['\\[68\\]', '36'], is_latex: true },
          { alternatives: ['52'], is_latex: false },
        ],
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(screen.getByText(/第1空/)).toBeInTheDocument()
    expect(screen.getByText(/第2空/)).toBeInTheDocument()
    expect(screen.getAllByText('68').length).toBeGreaterThan(0)
    expect(screen.getByText('52')).toBeInTheDocument()
  })

  it('falls back to old extractAnswerItems when answer_blanks missing', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>Simple</p>',
        answer: ['B'],
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(screen.getByText('B')).toBeInTheDocument()
  })

  it('renders structured analysis steps', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>Problem</p>',
        analysis_steps: [
          [
            { content: '<p>First step</p>', title: '<p>Hint</p>', step: 0 },
            { content: '<p>Second step</p>', title: '', step: 1 },
          ],
        ],
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('解析')).toBeInTheDocument())
    expect(screen.getByText('Hint')).toBeInTheDocument()
    expect(screen.getByText('First step')).toBeInTheDocument()
    expect(screen.getByText('Second step')).toBeInTheDocument()
  })

  it('falls back to raw analysis when analysis_steps missing', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>Problem</p>',
        analysis: 'Plain text analysis.',
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('解析')).toBeInTheDocument())
    expect(screen.getByText('Plain text analysis.')).toBeInTheDocument()
  })
})
