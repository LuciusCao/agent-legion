import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioPage } from './WorkflowStudioPage'

vi.mock('react-router-dom', () => ({
  useParams: () => ({ workspaceId: 'ws1' }),
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))

// Shape matches the existing catalog workflow API. Editable Studio will replace
// this with workspace active revision loading.
vi.mock('../api', () => ({
  fetchWorkflowDefinition: vi.fn().mockResolvedValue({
    workflow: {
      key: 'question_comprehension_info',
      label: '题目审题信息生成 DAG',
      intake: { modes: [] },
      edges: [],
      nodes: [
        {
          key: 'fetch_questions',
          label: '获取题目',
          capability: 'fetch_questions',
          after: [],
          inputs: [],
          outputs: ['questions.json'],
        },
      ],
    },
  }),
  validateWorkflowDraft: vi.fn().mockResolvedValue({ valid: true, errors: [] }),
  publishWorkflowDraft: vi.fn().mockResolvedValue({ valid: true, errors: [] }),
}))

describe('WorkflowStudioPage', () => {
  it('renders the workflow studio shell', async () => {
    render(<WorkflowStudioPage />)

    expect(await screen.findByText('Workflow Studio')).toBeInTheDocument()
    expect(await screen.findByText('题目审题信息生成 DAG')).toBeInTheDocument()
  })
})
