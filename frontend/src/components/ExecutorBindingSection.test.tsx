import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { ExecutorBindingSection } from './ExecutorBindingSection'
import { useSettingStore } from '../stores/settingStore'
import type { WorkflowDefinitionRecord } from '../types'
import type { ExecutorDefinition } from '../types/executorTypes'
import type { WorkspaceAgentRouteEntry } from '../hooks/useWorkspaceSettingsQuery'

// executorCatalog/agentRoutes/workflowDefinition 已迁入 react-query；
// 这里 mock 两个 query hook，draft（executorConfiguration）仍写 store。
const mockQueryData = vi.hoisted(() => ({
  executorCatalog: { current: [] as ExecutorDefinition[] },
  agentRoutes: { current: [] as WorkspaceAgentRouteEntry[] },
  workflowDefinition: { current: null as WorkflowDefinitionRecord | null },
}))

vi.mock('../hooks/useWorkspaceSettingsQuery', () => ({
  useWorkspaceSettingsSnapshot: () => ({
    workflowDefinition: mockQueryData.workflowDefinition.current,
    executorCatalog: mockQueryData.executorCatalog.current,
    agentRoutes: mockQueryData.agentRoutes.current,
  }),
}))

const catalog = [
  {
    id: 'code-default',
    kind: 'code' as const,
    capabilities: ['fetch_items', 'clean_items', 'mark_question'],
    global_capacity: 4,
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
      key: 'review_keywords',
      label: '审核关键词',
      capability: 'review_keywords',
      after: ['extract_keywords'],
      inputs: ['keywords.json'],
      outputs: ['keywords_review.json'],
    },
    {
      key: 'generate_distractors',
      label: '生成干扰项',
      capability: 'generate_distractors',
      after: ['review_difficulty'],
      inputs: ['difficulty.json'],
      outputs: ['distractors.json'],
    },
    {
      key: 'unsupported_node',
      label: '未支持节点',
      capability: 'unsupported_capability',
      after: [],
      inputs: [],
      outputs: [],
    },
  ],
}

function getSelectRoot(nodeKey: string) {
  return screen.getByTestId(`binding-select-${nodeKey}`)
}

function getSelectInput(nodeKey: string) {
  const root = getSelectRoot(nodeKey)
  const input = root.querySelector('input')
  if (!input) throw new Error(`Select input not found for ${nodeKey}`)
  return input as HTMLInputElement
}

function changeSelectValue(nodeKey: string, value: string) {
  const input = getSelectInput(nodeKey)
  act(() => {
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value'
    )?.set
    if (nativeInputValueSetter) {
      nativeInputValueSetter.call(input, value)
    } else {
      input.value = value
    }
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

describe('ExecutorBindingSection', () => {
  beforeEach(() => {
    mockQueryData.executorCatalog.current = catalog
    mockQueryData.agentRoutes.current = []
    mockQueryData.workflowDefinition.current = workflowDefinition
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
        ],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
    })
  })

  it('shows a binding select for every workflow node', () => {
    render(<ExecutorBindingSection />)

    expect(getSelectInput('fetch_items')).toBeInTheDocument()
    expect(getSelectInput('unsupported_node')).toBeInTheDocument()
    expect(getSelectInput('review_keywords')).toBeInTheDocument()
    expect(getSelectInput('generate_distractors')).toBeInTheDocument()
  })

  it('includes only allocated executors whose capabilities contain the node capability', () => {
    render(<ExecutorBindingSection />)

    // The rendered input values reflect the available options.
    expect(getSelectInput('fetch_items').value).toBe('')
    changeSelectValue('fetch_items', 'code-default')
    expect(getSelectInput('fetch_items').value).toBe('code-default')
  })

  it('matches local nodes by capability, independent of executor implementation', () => {
    mockQueryData.executorCatalog.current = [
      ...catalog,
      {
        id: 'alternate-local',
        kind: 'pi' as const,
        capabilities: ['fetch_items'],
        global_capacity: 1,
      },
    ]
    useSettingStore.setState({
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 4,
          },
          {
            executor_id: 'alternate-local',
            workspace_id: 'ws1',
            concurrency_limit: 1,
          },
        ],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
    })

    render(<ExecutorBindingSection />)

    // Both compatible executors are selectable.
    changeSelectValue('fetch_items', 'code-default')
    expect(getSelectInput('fetch_items').value).toBe('code-default')

    changeSelectValue('fetch_items', 'alternate-local')
    expect(getSelectInput('fetch_items').value).toBe('alternate-local')
  })

  it('displays a warning when no allocated executor supports the capability', () => {
    render(<ExecutorBindingSection />)

    expect(
      screen.getByText('没有已分配的执行器支持能力 unsupported_capability')
    ).toBeInTheDocument()
  })

  it('excludes agent nodes, which are routed by Agent ID', () => {
    mockQueryData.agentRoutes.current = [
      {
        workflow_key: 'sample_workflow',
        node_key: 'review_keywords',
        node_label: '审核关键词',
        capability: 'review_keywords',
        agent_id: 'keyword-reviewer',
        agent_skill: 'review_key_info',
      },
    ]

    render(<ExecutorBindingSection />)

    expect(
      screen.queryByTestId('binding-select-review_keywords')
    ).not.toBeInTheDocument()
    expect(getSelectInput('fetch_items')).toBeInTheDocument()
    expect(getSelectInput('generate_distractors')).toBeInTheDocument()
  })
})
