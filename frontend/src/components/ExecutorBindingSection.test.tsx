import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { ExecutorBindingSection } from './ExecutorBindingSection'
import { useSettingStore } from '../stores/settingStore'

const catalog = [
  {
    id: 'local-default',
    kind: 'local' as const,
    capabilities: ['fetch_questions', 'clean_and_parse', 'mark_question'],
    global_capacity: 4,
  },
  {
    id: 'pi-review',
    kind: 'pi' as const,
    capabilities: ['review_keywords', 'review_difficulty'],
    global_capacity: 2,
  },
  {
    id: 'openclaw-generate',
    kind: 'openclaw' as const,
    capabilities: ['generate_distractors', 'review_keywords'],
    global_capacity: 3,
  },
]

const workflowDefinition = {
  key: 'reading_analysis',
  label: '阅读分析',
  concurrency: { local: 8, agent: 2, nodes: {} },
  intake: { modes: [] },
  nodes: [
    {
      key: 'fetch_questions',
      label: '获取题目',
      capability: 'fetch_questions',
      runner: 'local' as const,
      after: [],
      inputs: [],
      outputs: ['questions.json'],
    },
    {
      key: 'review_keywords',
      label: '审核关键词',
      capability: 'review_keywords',
      runner: 'agent' as const,
      after: ['extract_keywords'],
      inputs: ['keywords.json'],
      outputs: ['keywords_review.json'],
    },
    {
      key: 'generate_distractors',
      label: '生成干扰项',
      capability: 'generate_distractors',
      runner: 'agent' as const,
      after: ['review_difficulty'],
      inputs: ['difficulty.json'],
      outputs: ['distractors.json'],
    },
    {
      key: 'unsupported_node',
      label: '未支持节点',
      capability: 'unsupported_capability',
      runner: 'agent' as const,
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
          {
            executor_id: 'pi-review',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
          {
            executor_id: 'openclaw-generate',
            workspace_id: 'ws1',
            concurrency_limit: 3,
          },
        ],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
    })
  })

  it('includes an explicit unbound option for each workflow node', () => {
    render(<ExecutorBindingSection />)

    for (const node of workflowDefinition.nodes) {
      const input = getSelectInput(node.key)
      expect(input).toBeInTheDocument()
    }
  })

  it('includes only allocated executors whose capabilities contain the node capability', () => {
    render(<ExecutorBindingSection />)

    // The rendered input values reflect the available options.
    expect(getSelectInput('fetch_questions').value).toBe('')
    changeSelectValue('fetch_questions', 'local-default')
    expect(getSelectInput('fetch_questions').value).toBe('local-default')

    expect(getSelectInput('review_keywords').value).toBe('')
    changeSelectValue('review_keywords', 'pi-review')
    expect(getSelectInput('review_keywords').value).toBe('pi-review')

    expect(getSelectInput('generate_distractors').value).toBe('')
    changeSelectValue('generate_distractors', 'openclaw-generate')
    expect(getSelectInput('generate_distractors').value).toBe(
      'openclaw-generate'
    )
  })

  it('allows switching the same node between compatible pi and openclaw executors', async () => {
    render(<ExecutorBindingSection />)

    changeSelectValue('review_keywords', 'pi-review')
    await waitFor(() => {
      expect(useSettingStore.getState().executorConfiguration.bindings).toEqual(
        [
          {
            workflow_key: 'reading_analysis',
            node_key: 'review_keywords',
            executor_id: 'pi-review',
          },
        ]
      )
    })

    changeSelectValue('review_keywords', 'openclaw-generate')
    await waitFor(() => {
      expect(useSettingStore.getState().executorConfiguration.bindings).toEqual(
        [
          {
            workflow_key: 'reading_analysis',
            node_key: 'review_keywords',
            executor_id: 'openclaw-generate',
          },
        ]
      )
    })
  })

  it('does not use legacy runner or agent engine to filter options', () => {
    useSettingStore.setState({
      executorCatalog: [
        ...catalog,
        {
          id: 'legacy-agent',
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
            executor_id: 'legacy-agent',
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

    changeSelectValue('fetch_questions', 'legacy-agent')
    expect(getSelectInput('fetch_questions').value).toBe('legacy-agent')
  })

  it('displays a warning when no allocated executor supports the capability', () => {
    render(<ExecutorBindingSection />)

    expect(
      screen.getByText('没有已分配的执行器支持能力 unsupported_capability')
    ).toBeInTheDocument()
  })
})
