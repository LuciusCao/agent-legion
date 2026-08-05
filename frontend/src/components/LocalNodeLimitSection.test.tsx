import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { LocalNodeLimitSection } from './LocalNodeLimitSection'
import { useSettingStore } from '../stores/settingStore'

const catalog = [
  {
    id: 'code-default',
    kind: 'code' as const,
    capabilities: ['fetch_questions', 'clean_and_parse', 'mark_question'],
    global_capacity: 4,
  },
  {
    id: 'pi-review',
    kind: 'pi' as const,
    capabilities: ['review_keywords'],
    global_capacity: 2,
  },
]

const workflowDefinition = {
  key: 'sample_workflow',
  label: '示例工作流',
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
    {
      key: 'clean_and_parse',
      label: '清洗与解析',
      capability: 'clean_and_parse',
      after: ['fetch_questions'],
      inputs: ['questions.json'],
      outputs: ['parsed.json'],
    },
    {
      key: 'review_keywords',
      label: '审核关键词',
      capability: 'review_keywords',
      after: ['extract_keywords'],
      inputs: ['keywords.json'],
      outputs: ['keywords_review.json'],
    },
    {
      key: 'unbound_node',
      label: '未绑定节点',
      capability: 'mark_question',
      after: [],
      inputs: [],
      outputs: [],
    },
  ],
}

// executorCatalog/workflowDefinition 已迁入 react-query；mock 快照 hook，
// draft（executorConfiguration）仍写 store。
vi.mock('../hooks/useWorkspaceSettingsQuery', () => ({
  useWorkspaceSettingsSnapshot: () => ({
    workflowDefinition,
    executorCatalog: catalog,
    agentRoutes: [],
  }),
}))

describe('LocalNodeLimitSection', () => {
  beforeEach(() => {
    useSettingStore.setState({
      workspaceId: 'ws1',
      settings: {
        entityType: 'question',
        intakeModes: [],
        labelOverrides: {},
        workflowKey: 'sample_workflow',
      },
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 4,
          },
          {
            executor_id: 'pi-review',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [
          {
            workflow_key: 'sample_workflow',
            node_key: 'fetch_questions',
            executor_id: 'code-default',
          },
          {
            workflow_key: 'sample_workflow',
            node_key: 'review_keywords',
            executor_id: 'pi-review',
          },
        ],
        node_limits: [
          {
            workflow_key: 'sample_workflow',
            node_key: 'fetch_questions',
            concurrency_limit: 2,
          },
        ],
        migration_warnings: [],
      },
    })
  })

  it('only renders nodes currently bound to a code executor', () => {
    render(<LocalNodeLimitSection />)

    expect(screen.getByText('获取题目')).toBeInTheDocument()
    expect(screen.queryByText('清洗与解析')).not.toBeInTheDocument()
    expect(screen.queryByText('审核关键词')).not.toBeInTheDocument()
    expect(screen.queryByText('未绑定节点')).not.toBeInTheDocument()
  })

  it('does not render agent-bound or unbound nodes', () => {
    render(<LocalNodeLimitSection />)

    expect(screen.queryByText('审核关键词')).not.toBeInTheDocument()
    expect(screen.queryByText('未绑定节点')).not.toBeInTheDocument()
  })

  it('sets the input max to the bound executor workspace allocation', () => {
    render(<LocalNodeLimitSection />)

    const input = screen.getByLabelText('获取题目 并发上限') as HTMLInputElement
    expect(input).toBeTruthy()
    expect(input).toHaveAttribute('max', '4')
  })

  it('updates the node limit through the store', async () => {
    render(<LocalNodeLimitSection />)

    const input = screen.getByLabelText('获取题目 并发上限') as HTMLInputElement
    fireEvent.change(input, { target: { value: '3' } })

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.node_limits
      ).toEqual([
        {
          workflow_key: 'sample_workflow',
          node_key: 'fetch_questions',
          concurrency_limit: 3,
        },
      ])
    })
  })

  it('removes the row from the request when the limit is cleared', async () => {
    render(<LocalNodeLimitSection />)

    const input = screen.getByLabelText('获取题目 并发上限') as HTMLInputElement
    fireEvent.change(input, { target: { value: '' } })

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.node_limits
      ).toEqual([])
    })
  })

  it('adds a limit row when a new code-bound node appears', async () => {
    useSettingStore.setState({
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 4,
          },
        ],
        bindings: [
          {
            workflow_key: 'sample_workflow',
            node_key: 'clean_and_parse',
            executor_id: 'code-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
      },
    })

    render(<LocalNodeLimitSection />)

    const input = screen.getByLabelText(
      '清洗与解析 并发上限'
    ) as HTMLInputElement
    fireEvent.change(input, { target: { value: '2' } })

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.node_limits
      ).toEqual([
        {
          workflow_key: 'sample_workflow',
          node_key: 'clean_and_parse',
          concurrency_limit: 2,
        },
      ])
    })
  })
})
