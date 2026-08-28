import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { LocalNodeLimitSection } from './LocalNodeLimitSection'
import { useSettingStore } from '../stores/settingStore'

// Agent 路由（review_keywords）经 agentRoutes 快照标注；其余节点一律 code 池。
const agentRoutes = [
  {
    workflow_key: 'sample_workflow',
    node_key: 'review_keywords',
    node_label: '审核关键词',
    capability: 'review_keywords',
    agent_id: 'reviewer-v1',
    agent_skill: 'demo/review',
  },
]

const workflowDefinition = {
  key: 'sample_workflow',
  label: '示例工作流',
  intake: { modes: [] },
  edges: [],
  nodes: [
    {
      key: 'fetch_items',
      label: '获取题目',
      capability: 'fetch_items',
      after: [],
      inputs: [],
      outputs: ['questions.json'],
    },
    {
      key: 'clean_items',
      label: '清洗与解析',
      capability: 'clean_items',
      after: ['fetch_items'],
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
  ],
}

// workflowDefinition/agentRoutes 已迁入 react-query；mock 快照 hook，
// draft（executorConfiguration）仍写 store。
vi.mock('../hooks/useWorkspaceSettingsQuery', () => ({
  useWorkspaceSettingsSnapshot: () => ({
    workflowDefinition,
    agentRoutes,
  }),
}))

describe('LocalNodeLimitSection', () => {
  beforeEach(() => {
    useSettingStore.setState({
      workspaceId: 'ws1',
      settings: {
        entityType: 'question',
        workflowKey: 'sample_workflow',
      },
      executorConfiguration: {
        node_limits: [
          {
            workflow_key: 'sample_workflow',
            node_key: 'fetch_items',
            concurrency_limit: 2,
          },
        ],
        migration_warnings: [],
        agent_capacity: null,
      },
    })
  })

  it('renders code-pool nodes and hides agent-routed ones', () => {
    render(<LocalNodeLimitSection />)

    expect(screen.getByText('获取题目')).toBeInTheDocument()
    expect(screen.getByText('清洗与解析')).toBeInTheDocument()
    expect(screen.queryByText('审核关键词')).not.toBeInTheDocument()
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
          node_key: 'fetch_items',
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

  it('adds a limit row for a previously unlimited code node', async () => {
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
          node_key: 'fetch_items',
          concurrency_limit: 2,
        },
        {
          workflow_key: 'sample_workflow',
          node_key: 'clean_items',
          concurrency_limit: 2,
        },
      ])
    })
  })
})
