import { fireEvent, render, screen } from '@testing-library/react'
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

describe('WorkflowStudioRightPanel', () => {
  it('contains only the selected node configuration', () => {
    const onClose = vi.fn()
    render(
      <WorkflowStudioRightPanel
        workflow={workflow}
        executorCatalog={executorCatalog}
        agentCatalog={[]}
        selectedNodeKey="fetch_questions"
        readOnly={false}
        definitionYaml="key: video_knowledge\n"
        setDefinitionYaml={vi.fn()}
        onClose={onClose}
      />
    )

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
})
