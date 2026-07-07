import React from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
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
      id: 'ws1:video_knowledge:v1',
      workspace_id: 'ws1',
      workflow_key: 'video_knowledge',
      version: 1,
      status: 'active',
      definition_hash: 'abcdef1234567890',
      created_at: '2026-07-02T00:00:00Z',
      published_at: '2026-07-02T00:00:00Z',
    },
    workflow: {
      key: 'video_knowledge',
      label: '知识视频 DAG',
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
    definition_yaml: 'key: video_knowledge\nlabel: 知识视频 DAG\n',
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
    compareWorkflowDraft: vi.fn().mockResolvedValue({
      valid: true,
      base_revision: {
        id: activeRevisionPayload.revision.id,
        workflow_key: activeRevisionPayload.revision.workflow_key,
        version: activeRevisionPayload.revision.version,
        definition_hash: activeRevisionPayload.revision.definition_hash,
      },
      draft_workflow: {
        key: activeRevisionPayload.workflow.key,
        label: activeRevisionPayload.workflow.label,
        version: activeRevisionPayload.revision.version + 1,
      },
      summary: {
        risk_level: 'info',
        node_changes: [
          {
            type: 'added',
            node_key: 'new_node',
            label: '新节点',
            fields: [],
            risk: 'info',
          },
        ],
        edge_changes: [],
        intake_changes: [],
        risk_flags: [],
      },
      errors: [],
    }),
  }
})

describe('WorkflowStudioPage', () => {
  it('renders the workflow studio shell', async () => {
    render(<WorkflowStudioPage />)

    expect(await screen.findByText('Workflow Studio')).toBeInTheDocument()
    expect(
      (await screen.findAllByText('知识视频 DAG')).length
    ).toBeGreaterThanOrEqual(1)
  })

  it('renders workflow identity and actions in the app bar without a second summary row', async () => {
    render(<WorkflowStudioPage />)

    const appBar = await screen.findByTestId('app-bar')
    expect(appBar).toHaveTextContent('Workflow Studio')
    expect(appBar).toHaveTextContent('知识视频 DAG')
    expect(appBar).toHaveTextContent('video_knowledge')
    expect(appBar).toHaveTextContent('v1')
    expect(appBar).toHaveTextContent('校验')
    expect(appBar).toHaveTextContent('发布')
    expect(appBar).toHaveTextContent('重置')
    expect(
      screen.queryByRole('region', { name: 'Workflow summary' })
    ).not.toBeInTheDocument()
  })

  it('renders active revision metadata and prefilled definition', async () => {
    const user = userEvent.setup()
    render(<WorkflowStudioPage />)

    expect(await screen.findByText('Workflow Studio')).toBeInTheDocument()
    expect(
      (await screen.findAllByText('知识视频 DAG')).length
    ).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('v1')[0]).toBeInTheDocument()
    expect(screen.getAllByText('abcdef12')[0]).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'YAML' }))
    expect(
      await screen.findByDisplayValue(/key: video_knowledge/)
    ).toBeInTheDocument()
  })

  it('marks the editor dirty and resets to active definition', async () => {
    const user = userEvent.setup()
    render(<WorkflowStudioPage />)

    await screen.findByText('Workflow Studio')
    await user.click(screen.getByRole('tab', { name: 'YAML' }))
    const editor = await screen.findByLabelText('高级 YAML 编辑器')
    await user.clear(editor)
    await user.type(editor, 'key: changed')

    const commandBar = screen.getByLabelText('Workflow command bar')
    expect(within(commandBar).getByText(/有未发布变更/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '重置' }))

    expect(screen.getByDisplayValue(/key: video_knowledge/)).toBeInTheDocument()
    expect(within(commandBar).getByText(/已同步/)).toBeInTheDocument()
  })

  it('opens publish review dialog before publishing', async () => {
    const user = userEvent.setup()
    render(<WorkflowStudioPage />)

    await screen.findByText('Workflow Studio')
    await user.click(screen.getByRole('tab', { name: 'YAML' }))
    const editor = screen.getByLabelText('高级 YAML 编辑器')
    await user.type(editor, '\n# edited')

    await screen.findByText(/有未发布变更/)
    const publishButton = screen.getByRole('button', { name: '发布' })
    await waitFor(() => expect(publishButton).not.toBeDisabled())
    await user.click(publishButton)

    expect(
      await screen.findByText('发布 workflow revision')
    ).toBeInTheDocument()
    expect(screen.getByText('确认发布')).toBeInTheDocument()
  })

  it('publishes after confirming the review dialog', async () => {
    const user = userEvent.setup()
    render(<WorkflowStudioPage />)

    await screen.findByText('Workflow Studio')
    await user.click(screen.getByRole('tab', { name: 'YAML' }))
    const editor = screen.getByLabelText('高级 YAML 编辑器')
    await user.type(editor, '\n# edited')

    await screen.findByText(/有未发布变更/)
    const publishButton = screen.getByRole('button', { name: '发布' })
    await waitFor(() => expect(publishButton).not.toBeDisabled())
    await user.click(publishButton)
    await screen.findByText('发布 workflow revision')

    await user.click(screen.getByRole('button', { name: '确认发布' }))

    expect(await screen.findByText('发布成功')).toBeInTheDocument()
  })
})
