import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowStudioRightPanel } from './WorkflowStudioRightPanel'
import { api } from '../../api'
import { TestQueryProvider } from '../../testing/testQueryClient'
import type { UserResponse } from '../../api/authApi'
import { useAuthStore } from '../../stores/authStore'
import { useSettingStore } from '../../stores/settingStore'
import type { WorkspaceSettings } from '../../types'

vi.mock('../../api', () => ({
  api: vi.fn(),
}))

vi.mock('../../api/executorApi', () => ({
  getExecutorCatalog: vi.fn().mockResolvedValue({ executors: [], agents: [] }),
}))

const mockApi = vi.mocked(api)

const adminUser: UserResponse = {
  id: 'u1',
  username: 'admin',
  display_name: 'Admin',
  role: 'admin',
  disabled_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

const workflow = {
  key: 'video_knowledge',
  label: '知识视频 DAG',
  intake: { modes: [] },
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
  edges: [],
}

const executorCatalog = [
  {
    id: 'code-default',
    kind: 'code' as const,
    global_capacity: 16,
    capabilities: ['fetch_questions'],
    capability_details: [
      {
        name: 'fetch_questions',
        path: 'workflow_nodes/fetch_questions.py',
      },
    ],
  },
]

const baseSettings: WorkspaceSettings = {
  entityType: 'question',
  intakeModes: [],
  labelOverrides: {},
  workflowKey: '',
}

function renderPanel(
  overrides?: Partial<Parameters<typeof WorkflowStudioRightPanel>[0]>
) {
  return render(
    <TestQueryProvider>
      <WorkflowStudioRightPanel
        workflow={workflow}
        executorCatalog={executorCatalog}
        agentCatalog={[]}
        selectedNodeKey="fetch_questions"
        readOnly={false}
        definitionYaml="key: video_knowledge\n"
        setDefinitionYaml={vi.fn()}
        onClose={vi.fn()}
        {...overrides}
      />
    </TestQueryProvider>
  )
}

describe('WorkflowStudioRightPanel', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useAuthStore.setState({ user: null, status: 'unknown' })
    useSettingStore.setState({ workspaceId: null, settings: baseSettings })
    mockApi.mockResolvedValue({
      origin: 'builtin',
      code: 'def run(inputs):\n    return {}\n',
      path: 'workflow_nodes/fetch_questions.py',
      version: null,
      has_draft: false,
    })
  })

  it('contains only the selected node configuration', () => {
    const onClose = vi.fn()
    renderPanel({ onClose })

    expect(screen.getByRole('region', { name: '节点配置' })).toBeInTheDocument()
    expect(screen.getByText('基本设置')).toBeInTheDocument()
    expect(screen.getByText('code-default')).toBeInTheDocument()
    expect(
      screen.getByText('workflow_nodes/fetch_questions.py')
    ).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByText('YAML 源码')).not.toBeInTheDocument()
    expect(
      screen.queryByLabelText('输入产物，每行一个')
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '数据契约 1 个产物' }))
    expect(screen.getByLabelText('输入产物，每行一个')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '关闭节点配置' }))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('shows code and config cards for a code-bound node with a config schema', async () => {
    useAuthStore.setState({ user: adminUser, status: 'authenticated' })
    useSettingStore.setState({
      workspaceId: 'ws1',
      settings: {
        ...baseSettings,
        workflowKey: 'video_knowledge',
        nodeConfig: { fetch_questions: { bank_version: 'v2' } },
        nodeConfigSchemas: {
          fetch_questions: {
            type: 'object',
            properties: {
              bank_version: { type: 'string', description: '题库版本' },
            },
          },
        },
      },
    })
    renderPanel()

    expect(screen.getByRole('region', { name: '节点代码' })).toBeInTheDocument()
    expect(
      await screen.findByText('def run(inputs):', { exact: false })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('region', { name: '节点配置 fetch_questions' })
    ).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'bank_version' })).toHaveValue(
      'v2'
    )
  })

  it('hides both cards when the node has no code path or config schema', () => {
    renderPanel({
      executorCatalog: [
        {
          ...executorCatalog[0],
          capability_details: [{ name: 'fetch_questions', path: null }],
        },
      ],
    })

    expect(
      screen.queryByRole('region', { name: '节点代码' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('region', { name: '节点配置 fetch_questions' })
    ).not.toBeInTheDocument()
  })
})
