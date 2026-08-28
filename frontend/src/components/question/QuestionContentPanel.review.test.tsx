import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { ReactElement } from 'react'
import { QuestionContentPanel } from './QuestionContentPanel'
import { TestQueryProvider } from '../../testing/testQueryClient'

function renderPanel(ui: ReactElement) {
  return render(ui, { wrapper: TestQueryProvider })
}

const mockFetchJobArtifact = vi.fn()

// panel 内部经 manifest 求 gate（issue #11 第 2 层）：detail 查询带终态
// generate/review 节点，等价于旧 props keyInfoPreviewable/ReviewAttempted。
// vi.mock factory 被 hoist 到 import 之前，fixture 数据用 vi.hoisted 定义。
const { GATE_NODES, GATE_NODES_NO_REVIEW } = vi.hoisted(() => ({
  GATE_NODES: [
    { node_key: 'generate_key_info', status: 'completed' },
    { node_key: 'generate_possible_errors', status: 'completed' },
    { node_key: 'review_key_info', status: 'completed' },
    { node_key: 'review_possible_errors', status: 'completed' },
  ],
  // 生成完成但评审未到终态：generate gate 开、review attempted 关。
  GATE_NODES_NO_REVIEW: [
    { node_key: 'generate_key_info', status: 'completed' },
    { node_key: 'generate_possible_errors', status: 'completed' },
    { node_key: 'review_key_info', status: 'running' },
    { node_key: 'review_possible_errors', status: 'pending' },
  ],
}))

let detailNodesOverride: Array<{ node_key: string; status: string }> | null = null

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

vi.mock('../../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../api')>()
  const { makeJobDetail } = await import('../../testing/fixtures')
  return {
    ...mod,
    fetchJobDetail: () =>
      Promise.resolve(makeJobDetail(detailNodesOverride ?? GATE_NODES)),
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
    renderPanel(
      <QuestionContentPanel
        jobId="job1"
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
    renderPanel(
      <QuestionContentPanel
        jobId="job1"
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
    renderPanel(
      <QuestionContentPanel
        jobId="job1"
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
    detailNodesOverride = GATE_NODES_NO_REVIEW
    renderPanel(<QuestionContentPanel jobId="job1" />)

    await waitFor(() =>
      expect(screen.getByText('审题信息')).toBeInTheDocument()
    )
    expect(mockFetchJobArtifact).not.toHaveBeenCalled()
    expect(screen.queryByTestId('CheckCircleIcon')).not.toBeInTheDocument()
    detailNodesOverride = null
  })
})
