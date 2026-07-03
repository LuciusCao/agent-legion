import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioPage } from './WorkflowStudioPage'
import { compareWorkflowDraft } from '../api'

vi.mock('react-router-dom', () => ({
  useParams: () => ({ workspaceId: 'ws1' }),
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))

const definitionYaml = vi.hoisted(
  () => `key: question_comprehension_info
label: 题目审题信息生成 DAG
schema_version: 2
intake:
  modes: {}
nodes:
  fetch_questions:
    label: 获取题目
    capability: fetch_questions
    after: []
    inputs: []
    outputs:
      - questions.json
  classify_comprehension_eligibility:
    label: 判断是否适合审题
    capability: classify_comprehension_eligibility
    after:
      - fetch_questions
    inputs:
      - questions.json
    outputs: []
  generate_key_info:
    label: 生成关键信息
    capability: generate_key_info
    after:
      - classify_comprehension_eligibility
    inputs:
      - questions.json
    outputs:
      - key_info.json
edges:
  - source: fetch_questions
    target: classify_comprehension_eligibility
    condition: null
  - source: classify_comprehension_eligibility
    target: generate_key_info
    condition:
      artifact: result.json
      path: $.eligible
      equals: true
`
)

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
          target: 'classify_comprehension_eligibility',
          condition: null,
        },
        {
          source: 'classify_comprehension_eligibility',
          target: 'generate_key_info',
          condition: {
            artifact: 'result.json',
            path: '$.eligible',
            equals: true,
          },
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
          key: 'classify_comprehension_eligibility',
          label: '判断是否适合审题',
          capability: 'classify_comprehension_eligibility',
          after: ['fetch_questions'],
          inputs: ['questions.json'],
          outputs: [],
        },
        {
          key: 'generate_key_info',
          label: '生成关键信息',
          capability: 'generate_key_info',
          after: ['classify_comprehension_eligibility'],
          inputs: ['questions.json'],
          outputs: ['key_info.json'],
        },
      ],
    },
    definition_yaml: definitionYaml,
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
        metadata_changes: [],
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
    expect(await screen.findByText('题目审题信息生成 DAG')).toBeInTheDocument()
  })

  it('renders active revision metadata and prefilled definition', async () => {
    render(<WorkflowStudioPage />)

    expect(await screen.findByText('Workflow Studio')).toBeInTheDocument()
    expect(await screen.findByText('题目审题信息生成 DAG')).toBeInTheDocument()
    expect(screen.getAllByText('v2')[0]).toBeInTheDocument()
    expect(screen.getAllByText('abcdef12')[0]).toBeInTheDocument()
    expect(
      await screen.findByDisplayValue(/key: question_comprehension_info/)
    ).toBeInTheDocument()
  })

  it('marks the editor dirty and resets to active definition', async () => {
    const user = userEvent.setup()
    render(<WorkflowStudioPage />)

    const editor = await screen.findByLabelText('高级 YAML 编辑器')
    await user.clear(editor)
    await user.type(editor, 'key: changed')

    expect(screen.getByText('有未保存修改')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '重置' }))

    expect(
      screen.getByDisplayValue(/key: question_comprehension_info/)
    ).toBeInTheDocument()
    expect(screen.getByText('已同步')).toBeInTheDocument()
  })

  it('opens publish review dialog before publishing', async () => {
    const user = userEvent.setup()
    render(<WorkflowStudioPage />)

    await screen.findByText('Workflow Studio')
    const editor = screen.getByLabelText('高级 YAML 编辑器')
    await user.type(editor, '\n# edited')

    await screen.findByText('有未发布变更')
    await user.click(screen.getByRole('button', { name: '发布' }))

    expect(
      await screen.findByText('发布 workflow revision')
    ).toBeInTheDocument()
    expect(screen.getByText('确认发布')).toBeInTheDocument()
  })

  it('publishes after confirming the review dialog', async () => {
    const user = userEvent.setup()
    render(<WorkflowStudioPage />)

    await screen.findByText('Workflow Studio')
    const editor = screen.getByLabelText('高级 YAML 编辑器')
    await user.type(editor, '\n# edited')

    await screen.findByText('有未发布变更')
    await user.click(screen.getByRole('button', { name: '发布' }))
    await screen.findByText('发布 workflow revision')

    await user.click(screen.getByRole('button', { name: '确认发布' }))

    expect(await screen.findByText('发布成功')).toBeInTheDocument()
  })

  it('edits workflow label and shows metadata changes in publish review', async () => {
    vi.mocked(compareWorkflowDraft).mockResolvedValue({
      valid: true,
      base_revision: null,
      draft_workflow: null,
      summary: {
        risk_level: 'info',
        node_changes: [],
        edge_changes: [],
        intake_changes: [],
        metadata_changes: [
          {
            type: 'modified',
            field: 'label',
            before_value: '题目审题信息生成 DAG',
            after_value: '题目审题信息生成 DAG v2',
            risk: 'info',
          },
        ],
        risk_flags: [],
      },
      errors: [],
    })

    render(<WorkflowStudioPage />)
    await screen.findByText('Workflow Studio')

    fireEvent.change(screen.getByLabelText('Workflow 名称'), {
      target: { value: '题目审题信息生成 DAG v2' },
    })

    await screen.findByText('有未发布变更')
    await userEvent.click(screen.getByRole('button', { name: '发布' }))

    expect(
      await screen.findByText('发布 workflow revision')
    ).toBeInTheDocument()
    expect(screen.getAllByText('元数据变更').length).toBeGreaterThanOrEqual(1)
  })

  it('edits node label and reflects the change in YAML and outline', async () => {
    vi.mocked(compareWorkflowDraft).mockResolvedValue({
      valid: true,
      base_revision: null,
      draft_workflow: null,
      summary: {
        risk_level: 'info',
        node_changes: [
          {
            type: 'modified',
            node_key: 'fetch_questions',
            label: '获取题目 v2',
            fields: ['label'],
            risk: 'info',
          },
        ],
        edge_changes: [],
        intake_changes: [],
        metadata_changes: [],
        risk_flags: [],
      },
      errors: [],
    })

    render(<WorkflowStudioPage />)
    await screen.findByText('Workflow Studio')

    await userEvent.click(screen.getAllByText('获取题目')[0])

    fireEvent.change(screen.getByLabelText('节点名称'), {
      target: { value: '获取题目 v2' },
    })

    await screen.findByText('有未发布变更')

    expect(
      (screen.getByLabelText('高级 YAML 编辑器') as HTMLTextAreaElement).value
    ).toContain('label: 获取题目 v2')
    expect(screen.getByText('改动')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '发布' }))

    expect(
      await screen.findByText('发布 workflow revision')
    ).toBeInTheDocument()
    expect(screen.getAllByText('节点变更').length).toBeGreaterThanOrEqual(1)
  })

  it('edits branch condition and shows high risk', async () => {
    vi.mocked(compareWorkflowDraft).mockResolvedValue({
      valid: true,
      base_revision: null,
      draft_workflow: null,
      summary: {
        risk_level: 'breaking',
        node_changes: [],
        edge_changes: [
          {
            type: 'condition_changed',
            source: 'classify_comprehension_eligibility',
            target: 'generate_key_info',
            before_condition: '$.eligible == true',
            after_condition: '$.eligible == false',
            risk: 'breaking',
          },
        ],
        intake_changes: [],
        metadata_changes: [],
        risk_flags: [
          {
            code: 'edge_condition_changed',
            severity: 'breaking',
            message: '分支条件变化会改变运行路径。',
          },
        ],
      },
      errors: [],
    })

    render(<WorkflowStudioPage />)
    await screen.findByText('Workflow Studio')

    await userEvent.click(screen.getAllByText('判断是否适合审题')[0])

    fireEvent.change(screen.getByLabelText('条件 equals'), {
      target: { value: 'false' },
    })

    await screen.findByText('存在高风险变更')

    expect(screen.getByText('风险等级: 高风险')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '发布' }))

    expect(
      await screen.findByText('发布 workflow revision')
    ).toBeInTheDocument()
  })
})
