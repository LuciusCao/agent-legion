import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkflowNodeOutline } from './WorkflowNodeOutline'
import type { WorkflowDefinitionRecord } from '../../types'

function makeWorkflow(
  nodes: {
    key: string
    label?: string
    capability?: string
    terminalOutcome?: string
  }[],
  edges: { source: string; target: string }[]
): WorkflowDefinitionRecord {
  return {
    key: 'question_comprehension_info',
    label: '题目审题信息生成 DAG',
    intake: { modes: [] },
    nodes: nodes.map((node) => ({
      key: node.key,
      label: node.label ?? node.key,
      capability: node.capability ?? 'cap',
      after: [],
      inputs: [],
      outputs: [],
      terminal: node.terminalOutcome
        ? { outcome: node.terminalOutcome }
        : undefined,
    })),
    edges,
  }
}

describe('WorkflowNodeOutline', () => {
  it('renders source node before downstream nodes', () => {
    const workflow = makeWorkflow(
      [
        { key: 'assemble', label: '组装' },
        { key: 'fetch', label: '获取' },
        { key: 'classify', label: '分类' },
      ],
      [
        { source: 'fetch', target: 'classify' },
        { source: 'classify', target: 'assemble' },
      ]
    )

    render(
      <WorkflowNodeOutline
        workflow={workflow}
        selectedNodeKey={null}
        onSelectNode={vi.fn()}
      />
    )

    const buttons = screen.getAllByRole('button')
    expect(buttons[0]).toHaveTextContent('获取')
    expect(buttons[1]).toHaveTextContent('分类')
    expect(buttons[2]).toHaveTextContent('组装')
  })

  it('shows entry, branch, terminal, and changed badges', () => {
    const workflow = makeWorkflow(
      [
        { key: 'entry', label: '入口节点' },
        { key: 'branch', label: '分支节点' },
        { key: 'leaf_one', label: '叶子一' },
        { key: 'leaf_two', label: '叶子二' },
      ],
      [
        { source: 'entry', target: 'branch' },
        { source: 'branch', target: 'leaf_one' },
        { source: 'branch', target: 'leaf_two' },
      ]
    )

    render(
      <WorkflowNodeOutline
        workflow={workflow}
        selectedNodeKey={null}
        onSelectNode={vi.fn()}
        changedNodeKeys={new Set(['branch'])}
      />
    )

    expect(screen.getByText('入口节点')).toBeInTheDocument()
    expect(screen.getByText('分支节点')).toBeInTheDocument()
    expect(screen.getAllByText('终点')).toHaveLength(2)
    expect(screen.getByText('改动')).toBeInTheDocument()
    expect(screen.getByText('入口')).toBeInTheDocument()
    expect(screen.getByText('分支')).toBeInTheDocument()
  })

  it('shows terminal outcome badge', () => {
    const workflow = makeWorkflow(
      [{ key: 'terminal', label: '终点节点', terminalOutcome: 'uploadable' }],
      []
    )

    render(
      <WorkflowNodeOutline
        workflow={workflow}
        selectedNodeKey={null}
        onSelectNode={vi.fn()}
      />
    )

    expect(screen.getByText('uploadable')).toBeInTheDocument()
  })

  it('calls onSelectNode when a node is clicked', async () => {
    const onSelectNode = vi.fn()
    const workflow = makeWorkflow([{ key: 'a', label: 'A' }], [])

    render(
      <WorkflowNodeOutline
        workflow={workflow}
        selectedNodeKey={null}
        onSelectNode={onSelectNode}
      />
    )

    await userEvent.click(screen.getByRole('button', { name: /A/ }))

    expect(onSelectNode).toHaveBeenCalledWith('a')
  })

  it('shows warning for disconnected nodes', () => {
    const workflow = makeWorkflow(
      [
        { key: 'a', label: 'A' },
        { key: 'b', label: 'B' },
        { key: 'lonely', label: '孤立' },
      ],
      [{ source: 'a', target: 'b' }]
    )

    render(
      <WorkflowNodeOutline
        workflow={workflow}
        selectedNodeKey={null}
        onSelectNode={vi.fn()}
      />
    )

    expect(screen.getByText(/1 个节点未连接到主流程/)).toBeInTheDocument()
  })
})
