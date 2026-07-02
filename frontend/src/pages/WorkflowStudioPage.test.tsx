import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioPage } from './WorkflowStudioPage'

vi.mock('react-router-dom', () => ({
  useParams: () => ({ workspaceId: 'ws1' }),
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))

vi.mock('../api', () => {
  const activeRevisionPayload = {
    revision: {
      id: 'ws1:question_comprehension_info:v2',
      workspace_id: 'ws1',
      workflow_key: 'question_comprehension_info',
      version: 2,
      status: 'active',
      definition_hash: 'abcdef1234567890',
      created_at: '2026-07-02T00:00:00Z',
      published_at: '2026-07-02T00:00:00Z',
    },
    workflow: {
      key: 'question_comprehension_info',
      label: '题目审题信息生成 DAG',
      intake: { modes: [] },
      edges: [
        {
          source: 'fetch_questions',
          target: 'clean_and_parse',
          condition: null,
        },
      ],
      nodes: [
        {
          key: 'fetch_questions',
          label: '获取题目',
          capability: 'fetch_questions',
          after: [],
          inputs: [],
          outputs: ['questions.json'],
        },
        {
          key: 'clean_and_parse',
          label: '清洗与解析',
          capability: 'clean_and_parse',
          after: ['fetch_questions'],
          inputs: ['questions.json'],
          outputs: ['questions_parsed.json'],
        },
      ],
    },
    definition_yaml:
      'key: question_comprehension_info\nlabel: 题目审题信息生成 DAG\n',
  }

  return {
    fetchActiveWorkflowRevision: vi
      .fn()
      .mockResolvedValue(activeRevisionPayload),
    fetchWorkflowRevisions: vi.fn().mockResolvedValue({
      revisions: [activeRevisionPayload.revision],
    }),
    fetchWorkflowDefinition: vi.fn(),
    publishWorkflowDraft: vi
      .fn()
      .mockResolvedValue({ valid: true, errors: [] }),
    validateWorkflowDraft: vi
      .fn()
      .mockResolvedValue({ valid: true, errors: [] }),
  }
})

describe('WorkflowStudioPage', () => {
  it('renders the workflow studio shell', async () => {
    render(<WorkflowStudioPage />)

    expect(await screen.findByText('Workflow Studio')).toBeInTheDocument()
    expect(await screen.findByText('题目审题信息生成 DAG')).toBeInTheDocument()
  })

  it('renders active revision metadata and prefilled definition', async () => {
    render(<WorkflowStudioPage />)

    expect(await screen.findByText('Workflow Studio')).toBeInTheDocument()
    expect(await screen.findByText('题目审题信息生成 DAG')).toBeInTheDocument()
    expect(screen.getAllByText('v2')[0]).toBeInTheDocument()
    expect(screen.getAllByText('abcdef12')[0]).toBeInTheDocument()
    expect(
      screen.getByDisplayValue(/key: question_comprehension_info/)
    ).toBeInTheDocument()
  })

  it('marks the editor dirty and resets to active definition', async () => {
    const user = userEvent.setup()
    render(<WorkflowStudioPage />)

    const editor = await screen.findByLabelText('Workflow definition')
    await user.clear(editor)
    await user.type(editor, 'key: changed')

    expect(screen.getByText('有未保存修改')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '重置为当前版本' }))

    expect(
      screen.getByDisplayValue(/key: question_comprehension_info/)
    ).toBeInTheDocument()
    expect(screen.getByText('已同步')).toBeInTheDocument()
  })

  it('shows publish success after clicking publish', async () => {
    const user = userEvent.setup()
    render(<WorkflowStudioPage />)

    await screen.findByText('Workflow Studio')
    const editor = screen.getByLabelText('Workflow definition')
    await user.type(editor, '\n# edited')
    await user.click(screen.getByRole('button', { name: '发布' }))

    expect(await screen.findByText('发布成功')).toBeInTheDocument()
  })
})
