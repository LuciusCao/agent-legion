import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioRightPanel } from './WorkflowStudioRightPanel'

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
    id: 'local-default',
    kind: 'local' as const,
    global_capacity: 16,
    capabilities: ['fetch_questions'],
    capability_details: [
      {
        name: 'fetch_questions',
        handler: 'question_comprehension_info.fetch_questions',
      },
    ],
  },
]

describe('WorkflowStudioRightPanel', () => {
  it('contains only the selected node configuration', () => {
    render(
      <WorkflowStudioRightPanel
        workflow={workflow}
        executorCatalog={executorCatalog}
        selectedNodeKey="fetch_questions"
        readOnly={false}
        definitionYaml="key: video_knowledge\n"
        setDefinitionYaml={vi.fn()}
      />
    )

    expect(screen.getByRole('region', { name: '节点配置' })).toBeInTheDocument()
    expect(screen.getByText('编辑节点配置')).toBeInTheDocument()
    expect(screen.getByText('local-default')).toBeInTheDocument()
    expect(
      screen.getByText('question_comprehension_info.fetch_questions')
    ).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByText('YAML 源码')).not.toBeInTheDocument()
  })
})
