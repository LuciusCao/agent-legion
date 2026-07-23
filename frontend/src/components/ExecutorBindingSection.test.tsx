import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { ExecutorBindingSection } from './ExecutorBindingSection'
import { useSettingStore } from '../stores/settingStore'

const catalog = [
  {
    id: 'local-default',
    kind: 'local' as const,
    capabilities: ['fetch_questions', 'clean_and_parse', 'mark_question'],
    global_capacity: 4,
  },
]

const workflowDefinition = {
  key: 'sample_workflow',
  label: '示例工作流',
  concurrency: { local: 8, agent: 2, nodes: {} },
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
    useSettingStore.setState({
      workspaceId: 'ws1',
      executorCatalog: catalog,
      workflowDefinition,
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
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

    expect(getSelectInput('fetch_questions')).toBeInTheDocument()
    expect(getSelectInput('unsupported_node')).toBeInTheDocument()
    expect(getSelectInput('review_keywords')).toBeInTheDocument()
    expect(getSelectInput('generate_distractors')).toBeInTheDocument()
  })

  it('includes only allocated executors whose capabilities contain the node capability', () => {
    render(<ExecutorBindingSection />)

    // The rendered input values reflect the available options.
    expect(getSelectInput('fetch_questions').value).toBe('')
    changeSelectValue('fetch_questions', 'local-default')
    expect(getSelectInput('fetch_questions').value).toBe('local-default')
  })

  it('matches local nodes by capability, independent of executor implementation', () => {
    useSettingStore.setState({
      executorCatalog: [
        ...catalog,
        {
          id: 'alternate-local',
          kind: 'pi' as const,
          capabilities: ['fetch_questions'],
          global_capacity: 1,
        },
      ],
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
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
    changeSelectValue('fetch_questions', 'local-default')
    expect(getSelectInput('fetch_questions').value).toBe('local-default')

    changeSelectValue('fetch_questions', 'alternate-local')
    expect(getSelectInput('fetch_questions').value).toBe('alternate-local')
  })

  it('displays a warning when no allocated executor supports the capability', () => {
    render(<ExecutorBindingSection />)

    expect(
      screen.getByText('没有已分配的执行器支持能力 unsupported_capability')
    ).toBeInTheDocument()
  })
})
