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

function getSelectForNode(nodeKey: string) {
  return document.querySelector(
    `md-outlined-select[aria-label="绑定 ${nodeKey}"]`
  ) as HTMLElement
}

function getOptions(select: HTMLElement) {
  return Array.from(select.querySelectorAll('md-select-option')).map((opt) =>
    opt.getAttribute('value')
  )
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
      const select = getSelectForNode(node.key)
      expect(select).toBeTruthy()
      const options = getOptions(select)
      expect(options).toContain('')
      expect(
        select.querySelector('md-select-option[value=""]')?.textContent
      ).toContain('未绑定')
    }
  })

  it('includes only allocated executors whose capabilities contain the node capability', () => {
    render(<ExecutorBindingSection />)

    const fetchSelect = getSelectForNode('fetch_questions')
    expect(getOptions(fetchSelect)).toEqual(['', 'local-default'])

    const reviewSelect = getSelectForNode('review_keywords')
    expect(getOptions(reviewSelect)).toEqual([
      '',
      'pi-review',
      'openclaw-generate',
    ])

    const generateSelect = getSelectForNode('generate_distractors')
    expect(getOptions(generateSelect)).toEqual(['', 'openclaw-generate'])
  })

  it('allows switching the same node between compatible pi and openclaw executors', async () => {
    render(<ExecutorBindingSection />)

    const reviewSelect = getSelectForNode('review_keywords')

    await act(async () => {
      reviewSelect.dispatchEvent(
        new CustomEvent('change', {
          detail: { value: 'pi-review' },
          bubbles: true,
        })
      )
    })
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

    await act(async () => {
      reviewSelect.dispatchEvent(
        new CustomEvent('change', {
          detail: { value: 'openclaw-generate' },
          bubbles: true,
        })
      )
    })
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

    const fetchSelect = getSelectForNode('fetch_questions')
    const options = getOptions(fetchSelect)
    // fetch_questions node has runner: 'local', but legacy-agent is a pi executor.
    // It must still appear because its capability list matches the node capability.
    expect(options).toContain('local-default')
    expect(options).toContain('legacy-agent')
  })

  it('displays a warning when no allocated executor supports the capability', () => {
    render(<ExecutorBindingSection />)

    expect(
      screen.getByText('没有已分配的执行器支持能力 unsupported_capability')
    ).toBeInTheDocument()
  })
})
