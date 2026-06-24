import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QuestionContentPanel } from './QuestionContentPanel'

const mockFetchJobArtifact = vi.fn()

const realQuestions = {
  content: JSON.stringify({
    questions: [
      {
        question_id: '7e199a41a45e93ff8a0460ffd99e65df',
        normalized: {
          stem: '<p>学校订了24箱牛奶，每箱10盒，每盒15袋，全校一共订了___1___袋牛奶。</p>',
          answer: [[{ content: '3600' }]],
          answer_blanks: [{ alternatives: ['3600'], is_latex: true }],
          analysis_steps: [
            [
              { content: '<p>先求总盒数</p>', title: '<p>提示</p>', step: 0 },
              { content: '<p>24×10×15=3600</p>', title: '', step: 1 },
            ],
          ],
        },
      },
    ],
  }),
}

const realComprehension = {
  content: JSON.stringify({
    question_id: '7e199a41a45e93ff8a0460ffd99e65df',
    fingerprint: null,
    fingerprint_source: 'missing',
    fingerprint_missing: true,
    comprehension_data: {
      comprehension_difficulty: 38,
      key_info_list: [
        {
          key_info_id: 'ki_001',
          type: 'given',
          content: { text: '24箱牛奶', position: { start: 4, end: 9 } },
          question: { text: '', options: [] },
          question_comprehension_ability: 'information_locating',
        },
        {
          key_info_id: 'ki_002',
          type: 'given',
          content: { text: '每箱10盒', position: { start: 10, end: 15 } },
          question: { text: '', options: [] },
          question_comprehension_ability: 'information_locating',
        },
        {
          key_info_id: 'ki_003',
          type: 'given',
          content: { text: '每盒15袋', position: { start: 16, end: 21 } },
          question: { text: '', options: [] },
          question_comprehension_ability: 'information_locating',
        },
        {
          key_info_id: 'ki_004',
          type: 'hidden',
          content: {
            derived_text: '学校一共订了240盒牛奶',
            position: { start: 4, end: 15 },
            derivation: '24箱 × 10盒/箱 = 240盒',
          },
          question: { text: '', options: [] },
          question_comprehension_ability: 'condition_sequencing',
        },
        {
          key_info_id: 'ki_005',
          type: 'hidden',
          content: {
            derived_text: '学校一共订了3600袋牛奶',
            position: { start: 16, end: 38 },
            derivation: '240盒 × 15袋/盒 = 3600袋',
          },
          question: { text: '', options: [] },
          question_comprehension_ability: 'relationship_identification',
        },
        {
          key_info_id: 'ki_006',
          type: 'hidden',
          content: {
            derived_text: '题目要求用“袋”作单位求出牛奶总数',
            position: { start: 35, end: 38 },
            derivation: '题干末尾为“...袋牛奶”，因此答案应以袋为单位',
          },
          question: { text: '', options: [] },
          question_comprehension_ability: 'answer_type_recognition',
        },
      ],
      possible_error_list: [
        {
          error_id: 'pe_001',
          error_type: 'question_comprehension',
          error_answer: ['240'],
          error_description:
            '学生把“盒”误当作最终单位，只计算出24箱共有多少盒牛奶（24×10），漏看了“每盒15袋”和题目末尾要求的“袋”。',
          related_key_info_ids: ['ki_003', 'ki_005', 'ki_006'],
        },
        {
          error_id: 'pe_002',
          error_type: 'question_comprehension',
          error_answer: ['360'],
          error_description:
            '学生跳过了“先求总盒数”的中间步骤，直接用箱数乘每盒袋数（24×15），说明没有理解数量之间的层级关系。',
          related_key_info_ids: ['ki_002', 'ki_003', 'ki_004'],
        },
        {
          error_id: 'pe_003',
          error_type: 'question_comprehension',
          error_answer: ['150'],
          error_description:
            '学生只计算了一盒牛奶有多少袋（10×15），忽略了学校一共订了24箱这个总量条件。',
          related_key_info_ids: ['ki_001', 'ki_005'],
        },
        {
          error_id: 'pe_004',
          error_type: 'question_comprehension',
          error_answer: ['49'],
          error_description:
            '学生把题目中的三个数直接相加（24+10+15），没有理解“每箱”“每盒”表示的是乘法关系。',
          related_key_info_ids: ['ki_001', 'ki_002', 'ki_003', 'ki_004'],
        },
        {
          error_id: 'pe_005',
          error_type: 'question_comprehension',
          error_answer: ['3600盒'],
          error_description:
            '学生虽然算对了数值3600，但把最终单位写成了“盒”，没有注意到题目要求用“袋”作单位。',
          related_key_info_ids: ['ki_006'],
        },
        {
          error_id: 'pe_006',
          error_type: 'question_comprehension',
          error_answer: ['24箱'],
          error_description:
            '学生只从题干中找到了“24箱牛奶”这个已知条件，没有理解题目真正要求计算的是总袋数。',
          related_key_info_ids: ['ki_001', 'ki_006'],
        },
      ],
    },
  }),
}

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return {
    ...mod,
    fetchJobArtifact: (...args: unknown[]) => {
      const artifactName = args[1] as string
      if (artifactName === 'comprehension_info.json') {
        return Promise.resolve(realComprehension)
      }
      if (artifactName === 'questions.json') {
        return Promise.resolve(realQuestions)
      }
      return mockFetchJobArtifact(...args)
    },
  }
})

describe('QuestionContentPanel regression', () => {
  beforeEach(() => {
    mockFetchJobArtifact.mockReset()
  })

  it('does not white screen when switching between possible errors', async () => {
    mockFetchJobArtifact.mockResolvedValue(realQuestions)
    render(<QuestionContentPanel jobId="job1" comprehensionCompleted />)
    await waitFor(() =>
      expect(screen.getByText('常见审题错误')).toBeInTheDocument()
    )
    const chips = screen.getAllByRole('button')
    const errorChips = chips.slice(6)
    expect(errorChips.length).toBe(6)

    // Click possible error 3
    fireEvent.click(errorChips[2])
    await waitFor(() => expect(screen.getByText('150')).toBeInTheDocument())

    // Click possible error 6 - previously caused white screen
    fireEvent.click(errorChips[5])
    await waitFor(() => expect(screen.getByText('24箱')).toBeInTheDocument())
  })

  it('keeps only one of key info or possible error active at a time', async () => {
    mockFetchJobArtifact.mockResolvedValue(realQuestions)
    render(<QuestionContentPanel jobId="job1" comprehensionCompleted />)
    await waitFor(() =>
      expect(screen.getByText('审题信息')).toBeInTheDocument()
    )
    const chips = screen.getAllByRole('button')
    const keyInfoChips = chips.slice(0, 6)
    const errorChips = chips.slice(6)

    // Activate key info 5 (ki_005)
    fireEvent.click(keyInfoChips[4])
    await waitFor(() =>
      expect(screen.getByText('隐含信息')).toBeInTheDocument()
    )

    // Activate possible error 3 - should deactivate key info detail
    fireEvent.click(errorChips[2])
    await waitFor(() => expect(screen.getByText('150')).toBeInTheDocument())
    expect(screen.queryByText('隐含信息')).not.toBeInTheDocument()

    // Activate key info 1 - should deactivate possible error detail
    fireEvent.click(keyInfoChips[0])
    await waitFor(() =>
      expect(screen.getByText('题干信息')).toBeInTheDocument()
    )
    expect(screen.queryByText('150')).not.toBeInTheDocument()
  })
})
