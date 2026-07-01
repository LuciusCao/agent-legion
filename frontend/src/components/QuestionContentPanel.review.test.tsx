import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QuestionContentPanel } from './QuestionContentPanel'

const mockFetchJobArtifact = vi.fn()

const mockComprehensionInfo = {
  question_id: 'Q1',
  fingerprint: 'fp1',
  fingerprint_source: 'cms',
  fingerprint_missing: false,
  comprehension_data: {
    comprehension_difficulty: 42,
    key_info_list: [
      {
        key_info_id: 'ki_001',
        type: 'given',
        content: { text: '方程 $x^2+mx+1=0$', position: { start: 9, end: 28 } },
        question: { text: '', options: [] },
        question_comprehension_ability: '识别方程结构',
      },
    ],
    possible_error_list: [
      {
        error_id: 'pe_001',
        error_type: 'question_comprehension',
        position: 1,
        error_answer: ['C'],
        error_description: '区间写反',
        related_key_info_ids: ['ki_001'],
      },
    ],
  },
}

function makeQuestionsJson() {
  return {
    content: JSON.stringify({
      questions: [
        {
          question_id: 'Q1',
          normalized: { stem: '<p>What is x?</p>' },
        },
      ],
    }),
  }
}

function makeKeyInfoReviewReportJson() {
  return {
    content: JSON.stringify({
      question_id: 'Q1',
      approved_count: 1,
      rejected_count: 0,
      warnings: [],
      decisions: [
        {
          key_info_id: 'ki_001',
          decision: 'approved',
          reason: '关键信息准确',
        },
      ],
    }),
  }
}

function makePossibleErrorsReviewReportJson() {
  return {
    content: JSON.stringify({
      question_id: 'Q1',
      approved_count: 0,
      rejected_count: 1,
      warnings: [],
      decisions: [
        {
          error_id: 'pe_001',
          decision: 'rejected',
          reason: '描述不够具体',
        },
      ],
    }),
  }
}

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return {
    ...mod,
    fetchJobArtifact: (...args: unknown[]) => {
      const artifactName = args[1] as string
      if (artifactName === 'comprehension_info.json') {
        return Promise.resolve({
          content: JSON.stringify(mockComprehensionInfo),
        })
      }
      if (artifactName === 'questions.json') {
        return Promise.resolve(makeQuestionsJson())
      }
      if (artifactName === 'key_info_review_report.json') {
        return Promise.resolve(makeKeyInfoReviewReportJson())
      }
      if (artifactName === 'possible_errors_review_report.json') {
        return Promise.resolve(makePossibleErrorsReviewReportJson())
      }
      return mockFetchJobArtifact(...args)
    },
  }
})

describe('QuestionContentPanel review integration', () => {
  beforeEach(() => {
    mockFetchJobArtifact.mockReset()
  })

  it('shows review status icons on key-info and possible-error chips', async () => {
    render(
      <QuestionContentPanel
        jobId="job1"
        keyInfoPreviewable
        possibleErrorsPreviewable
        keyInfoReviewAttempted
        possibleErrorsReviewAttempted
      />
    )

    await waitFor(() =>
      expect(screen.getByText('审题信息')).toBeInTheDocument()
    )

    const chips = screen.getAllByRole('button')
    expect(chips[0]).toContainElement(screen.getByTestId('CheckCircleIcon'))
    expect(chips[1]).toContainElement(screen.getByTestId('CloseIcon'))
  })

  it('renders key-info review decision and reason in detail card', async () => {
    render(
      <QuestionContentPanel
        jobId="job1"
        keyInfoPreviewable
        keyInfoReviewAttempted
      />
    )

    await waitFor(() =>
      expect(screen.getByText('审题信息')).toBeInTheDocument()
    )
    const chips = screen.getAllByRole('button')
    fireEvent.click(chips[0])

    await waitFor(() =>
      expect(screen.getByText('审核结果：通过')).toBeInTheDocument()
    )
    expect(screen.getByText('关键信息准确')).toBeInTheDocument()
  })

  it('renders possible-error review decision and reason in detail card', async () => {
    render(
      <QuestionContentPanel
        jobId="job1"
        possibleErrorsPreviewable
        possibleErrorsReviewAttempted
      />
    )

    await waitFor(() =>
      expect(screen.getByText('常见审题错误')).toBeInTheDocument()
    )
    const chip = screen.getByText('第1空：区间写反').closest('button')!
    fireEvent.click(chip)

    await waitFor(() =>
      expect(screen.getByText('审核结果：拒绝')).toBeInTheDocument()
    )
    expect(screen.getByText('描述不够具体')).toBeInTheDocument()
  })

  it('does not show review icons when review has not been attempted', async () => {
    render(
      <QuestionContentPanel
        jobId="job1"
        keyInfoPreviewable
        possibleErrorsPreviewable
      />
    )

    await waitFor(() =>
      expect(screen.getByText('审题信息')).toBeInTheDocument()
    )
    expect(mockFetchJobArtifact).not.toHaveBeenCalled()
    expect(screen.queryByTestId('CheckCircleIcon')).not.toBeInTheDocument()
  })
})
