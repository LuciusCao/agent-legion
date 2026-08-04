import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from '@testing-library/react'
import { QuestionContentPanel } from './QuestionContentPanel'

const mockFetchJobArtifact = vi.fn()

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

function makeComprehensionJson(info: Record<string, unknown>) {
  return { content: JSON.stringify(info) }
}

function makeKeyInfoReviewedJson() {
  return {
    content: JSON.stringify({
      question_id: mockComprehensionInfo.question_id,
      key_info_list: mockComprehensionInfo.comprehension_data.key_info_list,
    }),
  }
}

function makePossibleErrorsReviewedJson() {
  return {
    content: JSON.stringify({
      question_id: mockComprehensionInfo.question_id,
      possible_error_list:
        mockComprehensionInfo.comprehension_data.possible_error_list,
    }),
  }
}

function makeKeyInfoReviewReportJson() {
  return {
    content: JSON.stringify({
      question_id: mockComprehensionInfo.question_id,
      approved_count: 2,
      rejected_count: 0,
      warnings: [],
      decisions: [
        {
          key_info_id: 'ki_001',
          decision: 'approved',
          reason: '关键信息准确',
        },
        {
          key_info_id: 'ki_002',
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
      question_id: mockComprehensionInfo.question_id,
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
        content: {
          text: '方程 $x^2+mx+1=0$',
          position: { start: 9, end: 28 },
        },
        question: {
          text: '这个方程是什么类型？',
          options: [
            { label: 'A', text: '一元二次方程', is_correct: true },
            { label: 'B', text: '一次方程', is_correct: false },
          ],
        },
        question_comprehension_ability: '识别方程结构',
      },
      {
        key_info_id: 'ki_002',
        type: 'hidden',
        content: {
          derived_text: '判别式大于零',
          derivation: '有两个不等实根 $\\Leftrightarrow \\Delta>0$',
          position: { start: 44, end: 48 },
        },
        question: {
          text: '有两个不等实根的条件是什么？',
          options: [
            { label: 'A', text: '$\\Delta > 0$', is_correct: true },
            { label: 'B', text: '$\\Delta = 0$', is_correct: false },
          ],
        },
        question_comprehension_ability: '应用判别式',
      },
    ],
    possible_error_list: [
      {
        error_id: 'pe_001',
        error_type: 'question_comprehension',
        position: 1,
        error_answer: ['C'],
        error_description: '区间写反',
        related_key_info_ids: ['ki_002'],
      },
    ],
  },
}

let comprehensionArtifactEnabled = true

vi.mock('../../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../api')>()
  return {
    ...mod,
    fetchJobArtifact: (...args: unknown[]) => {
      const artifactName = args[1] as string
      if (artifactName === 'comprehension_info.json') {
        if (!comprehensionArtifactEnabled) {
          return Promise.reject(new Error('not found'))
        }
        return Promise.resolve(makeComprehensionJson(mockComprehensionInfo))
      }
      if (artifactName === 'key_info_reviewed.json') {
        return Promise.resolve(makeKeyInfoReviewedJson())
      }
      if (artifactName === 'key_info_raw.json') {
        return Promise.resolve(makeKeyInfoReviewedJson())
      }
      if (artifactName === 'possible_errors_reviewed.json') {
        return Promise.resolve(makePossibleErrorsReviewedJson())
      }
      if (artifactName === 'possible_errors_raw.json') {
        return Promise.resolve(makePossibleErrorsReviewedJson())
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

describe('QuestionContentPanel', () => {
  beforeEach(() => {
    mockFetchJobArtifact.mockReset()
    comprehensionArtifactEnabled = true
  })

  it('renders images embedded in stem html', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>下图中有___1___个平行四边形。</p><p><img src="https://example.com/diagram.png" alt="diagram"></p>',
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => {
      const img = document.querySelector(
        'img[src="https://example.com/diagram.png"]'
      )
      expect(img).toBeInTheDocument()
    })
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
    expect(within(listItems[1]).getByTestId('CheckIcon')).toBeInTheDocument()
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
    expect(within(listItems[1]).getByTestId('CheckIcon')).toBeInTheDocument()
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

  it('strips HTML paragraph tags from answer blank alternatives', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>Fill blank</p>',
        answer_blanks: [
          { alternatives: ['<p>\\(5200\\)</p>'], is_latex: true },
        ],
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('答案')).toBeInTheDocument())
    expect(screen.getByText(/第1空/)).toBeInTheDocument()
    expect(screen.queryByText('<p>')).not.toBeInTheDocument()
    expect(screen.queryByText('</p>')).not.toBeInTheDocument()
    expect(document.querySelector('.katex')).toBeInTheDocument()
  })

  it('strips HTML tags from inline options', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({
        stem: '<p>Pick one</p>',
        options: [{ label: 'A', content: '<p>\\(x\\)</p>' }],
      })
    )

    render(<QuestionContentPanel jobId="job1" />)
    await waitFor(() => expect(screen.getByText('选项')).toBeInTheDocument())
    expect(screen.queryByText('<p>')).not.toBeInTheDocument()
    expect(document.querySelector('.katex')).toBeInTheDocument()
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

  it('renders comprehension chips after data loads', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(<QuestionContentPanel jobId="job1" keyInfoPreviewable />)
    await waitFor(() => expect(screen.getByText('题干')).toBeInTheDocument())
    expect(screen.getByText('审题信息')).toBeInTheDocument()
    expect(screen.getByText('2 个信息点')).toBeInTheDocument()
    const chips = screen.getAllByRole('button')
    expect(chips.length).toBeGreaterThan(0)
    expect(chips[0]).toHaveTextContent('1')
    expect(chips[1]).toHaveTextContent('2')
  })

  it('does not render key-info chips before generate node completes', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(<QuestionContentPanel jobId="job1" keyInfoPreviewable={false} />)
    await waitFor(() => expect(screen.getByText('题干')).toBeInTheDocument())
    expect(screen.queryByText('审题信息')).not.toBeInTheDocument()
  })

  it('renders possible-error chips after data loads', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(<QuestionContentPanel jobId="job1" possibleErrorsPreviewable />)
    await waitFor(() => expect(screen.getByText('题干')).toBeInTheDocument())
    expect(screen.getByText('常见审题错误')).toBeInTheDocument()
    expect(screen.getByText('1 个易错点')).toBeInTheDocument()
    const chips = screen.getAllByRole('button')
    const possibleErrorChip = chips.find((c) =>
      c.textContent?.includes('区间写反')
    )
    expect(possibleErrorChip).toBeDefined()
  })

  it('does not render possible-error block before generate node completes', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(
      <QuestionContentPanel jobId="job1" possibleErrorsPreviewable={false} />
    )
    await waitFor(() => expect(screen.getByText('题干')).toBeInTheDocument())
    expect(screen.queryByText('常见审题错误')).not.toBeInTheDocument()
  })

  it('renders only key-info chips when only key-info is previewable', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(
      <QuestionContentPanel
        jobId="job1"
        keyInfoPreviewable
        possibleErrorsPreviewable={false}
      />
    )
    await waitFor(() => expect(screen.getByText('题干')).toBeInTheDocument())
    expect(screen.getByText('审题信息')).toBeInTheDocument()
    expect(screen.queryByText('常见审题错误')).not.toBeInTheDocument()
  })

  it('renders only possible-error chips when only possible-error is previewable', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(
      <QuestionContentPanel
        jobId="job1"
        keyInfoPreviewable={false}
        possibleErrorsPreviewable
      />
    )
    await waitFor(() => expect(screen.getByText('题干')).toBeInTheDocument())
    expect(screen.getByText('常见审题错误')).toBeInTheDocument()
    expect(screen.queryByText('审题信息')).not.toBeInTheDocument()
  })

  it('toggles possible-error chip selection and expands detail card', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(
      <QuestionContentPanel
        jobId="job1"
        keyInfoPreviewable
        possibleErrorsPreviewable
      />
    )
    await waitFor(() =>
      expect(screen.getByText('常见审题错误')).toBeInTheDocument()
    )
    const chips = screen.getAllByRole('button')
    const possibleErrorChip = chips.find((c) =>
      c.textContent?.includes('区间写反')
    )!
    fireEvent.click(possibleErrorChip)
    await waitFor(() =>
      expect(screen.getByText(/错误答案/)).toBeInTheDocument()
    )
    expect(screen.getByText('C')).toBeInTheDocument()
    expect(screen.getByText('第1空：区间写反')).toBeInTheDocument()
    expect(screen.getByText('区间写反')).toBeInTheDocument()
    expect(screen.getAllByText('判别式大于零').length).toBeGreaterThanOrEqual(2)

    fireEvent.click(possibleErrorChip)
    await waitFor(() =>
      expect(screen.queryByText(/错误答案/)).not.toBeInTheDocument()
    )
  })

  it('switches highlight from key-info selection to possible-error selection', async () => {
    const originalPositions =
      mockComprehensionInfo.comprehension_data.key_info_list.map(
        (k) => k.content.position
      )
    mockComprehensionInfo.comprehension_data.key_info_list[0].content.position =
      {
        start: 0,
        end: 15,
      }
    mockComprehensionInfo.comprehension_data.key_info_list[1].content.position =
      {
        start: 17,
        end: 20,
      }

    try {
      mockFetchJobArtifact.mockResolvedValue(
        makeQuestionsJson({
          stem: '方程 x^2+mx+1=0 有两个不等实根',
        })
      )

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
      const chips = screen.getAllByRole('button')
      const keyInfoChip = chips.find((c) => c.textContent?.includes('方程'))!
      fireEvent.click(keyInfoChip)
      await waitFor(() =>
        expect(document.querySelectorAll('.highlight').length).toBeGreaterThan(
          0
        )
      )

      const possibleErrorChip = chips.find((c) =>
        c.textContent?.includes('区间写反')
      )!
      fireEvent.click(possibleErrorChip)
      await waitFor(() =>
        expect(document.querySelectorAll('.highlight').length).toBeGreaterThan(
          0
        )
      )
      // Key-info detail is no longer shown because selections are mutually exclusive.
      expect(screen.queryByText('题干信息')).not.toBeInTheDocument()
    } finally {
      mockComprehensionInfo.comprehension_data.key_info_list.forEach(
        (k, idx) => {
          k.content.position = originalPositions[idx]
        }
      )
    }
  })

  it('highlights stem with HTML and LaTeX without leaking tags as text', async () => {
    const originalPositions =
      mockComprehensionInfo.comprehension_data.key_info_list.map(
        (k) => k.content.position
      )
    mockComprehensionInfo.comprehension_data.key_info_list[0].content.position =
      {
        start: 0,
        end: 15,
      }

    try {
      mockFetchJobArtifact.mockResolvedValue(
        makeQuestionsJson({
          stem: '<p>方程 <strong>$x^2+mx+1=0$</strong> 有两个不等实根</p>',
        })
      )

      render(<QuestionContentPanel jobId="job1" keyInfoPreviewable />)
      await waitFor(() =>
        expect(screen.getByText('审题信息')).toBeInTheDocument()
      )
      const chips = screen.getAllByRole('button')
      const keyInfoChip = chips.find((c) => c.textContent?.includes('方程'))!
      fireEvent.click(keyInfoChip)
      await waitFor(() =>
        expect(document.querySelectorAll('.highlight').length).toBeGreaterThan(
          0
        )
      )

      expect(screen.queryByText('<p>')).not.toBeInTheDocument()
      expect(screen.queryByText('<strong>')).not.toBeInTheDocument()
      expect(screen.queryByText('</strong>')).not.toBeInTheDocument()
      expect(document.querySelector('.katex')).toBeInTheDocument()
    } finally {
      mockComprehensionInfo.comprehension_data.key_info_list.forEach(
        (k, idx) => {
          k.content.position = originalPositions[idx]
        }
      )
    }
  })

  it('toggles chip selection and expands detail card', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(<QuestionContentPanel jobId="job1" keyInfoPreviewable />)
    await waitFor(() =>
      expect(screen.getByText('审题信息')).toBeInTheDocument()
    )
    const chips = screen.getAllByRole('button')
    fireEvent.click(chips[0])
    await waitFor(() =>
      expect(screen.getByText('题干信息')).toBeInTheDocument()
    )
    expect(screen.getByText('识别方程结构')).toBeInTheDocument()

    fireEvent.click(chips[0])
    await waitFor(() =>
      expect(screen.queryByText('题干信息')).not.toBeInTheDocument()
    )
  })

  it('shows derived_text in the annotation and derivation in the detail card for hidden key info', async () => {
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(<QuestionContentPanel jobId="job1" keyInfoPreviewable />)
    await waitFor(() =>
      expect(screen.getByText('审题信息')).toBeInTheDocument()
    )
    const chips = screen.getAllByRole('button')
    fireEvent.click(chips[1])
    await waitFor(() =>
      expect(screen.getByText('隐含信息')).toBeInTheDocument()
    )

    const detailCard = screen.getByText('隐含信息').parentElement?.parentElement
    expect(detailCard).toBeTruthy()
    expect(detailCard).toHaveTextContent(/有两个不等实根/)
    expect(
      within(detailCard as HTMLElement).queryByText(/判别式大于零/)
    ).not.toBeInTheDocument()

    const annotationCard = document.getElementById('annotation-ki_002')
    expect(annotationCard).toBeInTheDocument()
    expect(annotationCard).toHaveTextContent(/判别式大于零/)
    expect(annotationCard).not.toHaveTextContent(/有两个不等实根/)
  })

  it('renders chips from intermediate artifacts when comprehension_info.json is missing', async () => {
    comprehensionArtifactEnabled = false
    mockFetchJobArtifact.mockResolvedValue(
      makeQuestionsJson({ stem: '<p>What is x?</p>' })
    )

    render(
      <QuestionContentPanel
        jobId="job1"
        keyInfoPreviewable
        possibleErrorsPreviewable
      />
    )
    await waitFor(() => expect(screen.getByText('题干')).toBeInTheDocument())
    expect(screen.getByText('审题信息')).toBeInTheDocument()
    expect(screen.getByText('常见审题错误')).toBeInTheDocument()
  })

  describe('socratic question', () => {
    it('renders socratic question and options after selecting a key info chip', async () => {
      mockFetchJobArtifact.mockResolvedValue(
        makeQuestionsJson({ stem: '<p>What is x?</p>' })
      )

      render(
        <QuestionContentPanel
          jobId="job1"
          keyInfoPreviewable={true}
          keyInfoReviewAttempted={false}
          possibleErrorsReviewAttempted={false}
        />
      )

      await waitFor(() => {
        expect(screen.getByText('审题信息')).toBeInTheDocument()
      })

      const chips = screen.getAllByRole('button')
      fireEvent.click(chips[0])

      await waitFor(() => {
        expect(screen.getByText('苏格拉底提问')).toBeInTheDocument()
      })
      expect(screen.getByText('这个方程是什么类型？')).toBeInTheDocument()
      expect(screen.getByText('A.')).toBeInTheDocument()
      expect(screen.getByText('一元二次方程')).toBeInTheDocument()
      expect(screen.getByText('✓ 正确')).toBeInTheDocument()
    })

    it('hides socratic section when question text and options are empty', async () => {
      const originalQuestion =
        mockComprehensionInfo.comprehension_data.key_info_list[0].question
      mockComprehensionInfo.comprehension_data.key_info_list[0].question = {
        text: '',
        options: [],
      }

      try {
        mockFetchJobArtifact.mockResolvedValue(
          makeQuestionsJson({ stem: '<p>What is x?</p>' })
        )

        render(
          <QuestionContentPanel
            jobId="job1"
            keyInfoPreviewable={true}
            keyInfoReviewAttempted={false}
            possibleErrorsReviewAttempted={false}
          />
        )

        await waitFor(() => {
          expect(screen.getByText('审题信息')).toBeInTheDocument()
        })

        const chips = screen.getAllByRole('button')
        fireEvent.click(chips[0])

        await waitFor(() => {
          expect(screen.getByText('题干信息')).toBeInTheDocument()
        })
        expect(screen.queryByText('苏格拉底提问')).not.toBeInTheDocument()
      } finally {
        mockComprehensionInfo.comprehension_data.key_info_list[0].question =
          originalQuestion
      }
    })
  })

  describe('review status', () => {
    it('does not fetch review reports when review nodes are not completed', async () => {
      mockFetchJobArtifact.mockResolvedValue(
        makeQuestionsJson({ stem: '<p>What is x?</p>' })
      )

      render(
        <QuestionContentPanel
          jobId="job1"
          keyInfoPreviewable
          possibleErrorsPreviewable
          keyInfoReviewAttempted={false}
          possibleErrorsReviewAttempted={false}
        />
      )

      await waitFor(() =>
        expect(screen.getByText('审题信息')).toBeInTheDocument()
      )
      expect(
        mockFetchJobArtifact.mock.calls.some((call) =>
          String(call[1]).includes('review_report')
        )
      ).toBe(false)
    })

    it('shows approved icon on key info chip after review succeeds', async () => {
      mockFetchJobArtifact.mockResolvedValue(
        makeQuestionsJson({ stem: '<p>What is x?</p>' })
      )

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
      const keyInfoChip = chips[0]
      expect(
        within(keyInfoChip).getByTestId('CheckCircleIcon')
      ).toBeInTheDocument()
    })

    it('shows rejected detail status on possible error after review succeeds', async () => {
      mockFetchJobArtifact.mockResolvedValue(
        makeQuestionsJson({ stem: '<p>What is x?</p>' })
      )

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
      const chips = screen.getAllByRole('button')
      const possibleErrorChip = chips.find((c) =>
        c.textContent?.includes('区间写反')
      )!
      fireEvent.click(possibleErrorChip)
      await waitFor(() =>
        expect(screen.getByText(/审核结果/)).toBeInTheDocument()
      )
      expect(screen.getByText(/拒绝/)).toBeInTheDocument()
      expect(screen.getByText('描述不够具体')).toBeInTheDocument()
    })
  })
})
