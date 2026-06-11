import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QuestionContentPanel } from './QuestionContentPanel'

const mockFetchQuestionDetail = vi.fn()

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return {
    ...mod,
    fetchQuestionDetail: (...args: unknown[]) =>
      mockFetchQuestionDetail(...args),
  }
})

describe('QuestionContentPanel', () => {
  beforeEach(() => {
    mockFetchQuestionDetail.mockReset()
  })

  it('renders answer badges for array answer', async () => {
    mockFetchQuestionDetail.mockResolvedValue({
      question_id: 'Q1',
      title: 'Test',
      normalized: {
        stem: '<p>What is 1+1?</p>',
        options: [
          { label: 'A', content: '1' },
          { label: 'B', content: '2' },
        ],
        answer: ['B'],
      },
      cms_payload: null,
      jobs: [],
    })

    render(<QuestionContentPanel workspaceId="ws1" questionId="Q1" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(screen.getByText('B')).toBeInTheDocument()
  })

  it('renders answer badge with LaTeX', async () => {
    mockFetchQuestionDetail.mockResolvedValue({
      question_id: 'Q2',
      title: 'Test',
      normalized: {
        stem: '<p>What is x?</p>',
        answer: ['$x = 2$'],
      },
      cms_payload: null,
      jobs: [],
    })

    render(<QuestionContentPanel workspaceId="ws1" questionId="Q2" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(document.querySelector('.katex')).toBeInTheDocument()
  })

  it('shows check icon for correct option', async () => {
    mockFetchQuestionDetail.mockResolvedValue({
      question_id: 'Q3',
      title: 'Test',
      normalized: {
        stem: '<p>Pick one</p>',
        options: [
          { label: 'A', content: 'Wrong' },
          { label: 'B', content: 'Right' },
        ],
        answer: ['B'],
      },
      cms_payload: null,
      jobs: [],
    })

    render(<QuestionContentPanel workspaceId="ws1" questionId="Q3" />)
    await waitFor(() => expect(screen.getByText('选项')).toBeInTheDocument())
    const listItems = screen.getAllByRole('listitem')
    expect(listItems).toHaveLength(2)
    expect(listItems[1].querySelector('md-icon')).toHaveTextContent('check')
  })

  it('falls back to raw data for complex answer', async () => {
    mockFetchQuestionDetail.mockResolvedValue({
      question_id: 'Q4',
      title: 'Test',
      normalized: {
        stem: '<p>Complex</p>',
        answer: { nested: { value: 'deep' } },
      },
      cms_payload: null,
      jobs: [],
    })

    render(<QuestionContentPanel workspaceId="ws1" questionId="Q4" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(screen.getByText('无答案')).toBeInTheDocument()
  })

  it('marks correct option for single-string answer', async () => {
    mockFetchQuestionDetail.mockResolvedValue({
      question_id: 'Q5',
      title: 'Test',
      normalized: {
        stem: '<p>Pick one</p>',
        options: [
          { label: 'A', content: 'Wrong' },
          { label: 'B', content: 'Right' },
        ],
        answer: 'B',
      },
      cms_payload: null,
      jobs: [],
    })

    render(<QuestionContentPanel workspaceId="ws1" questionId="Q5" />)
    await waitFor(() => expect(screen.getByText('选项')).toBeInTheDocument())
    const listItems = screen.getAllByRole('listitem')
    expect(listItems[1].querySelector('md-icon')).toHaveTextContent('check')
  })

  it('renders structured answer blanks with alternatives', async () => {
    mockFetchQuestionDetail.mockResolvedValue({
      question_id: 'Q6',
      title: 'Test',
      normalized: {
        stem: '<p>Fill blanks</p>',
        answerBlanks: [
          { alternatives: ['\\[68\\]', '36'], isLatex: true },
          { alternatives: ['52'], isLatex: false },
        ],
      },
      cms_payload: null,
      jobs: [],
    })

    render(<QuestionContentPanel workspaceId="ws1" questionId="Q6" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(screen.getByText(/第1空/)).toBeInTheDocument()
    expect(screen.getByText(/第2空/)).toBeInTheDocument()
    expect(screen.getAllByText('68').length).toBeGreaterThan(0)
    expect(screen.getByText('52')).toBeInTheDocument()
  })

  it('falls back to old extractAnswerItems when answerBlanks missing', async () => {
    mockFetchQuestionDetail.mockResolvedValue({
      question_id: 'Q7',
      title: 'Test',
      normalized: {
        stem: '<p>Simple</p>',
        answer: ['B'],
      },
      cms_payload: null,
      jobs: [],
    })

    render(<QuestionContentPanel workspaceId="ws1" questionId="Q7" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(screen.getByText('B')).toBeInTheDocument()
  })
})
