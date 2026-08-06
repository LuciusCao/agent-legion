import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { JobFilterBar } from './JobFilterBar'
import {
  createJobSummary,
  createOptionAccumulator,
  useJobStore,
} from '../../stores/jobStore'
import type { WorkflowDefinitionRecord } from '../../types'
import type { JobSummary } from '../../types/jobTypes'

const workflowDefinition: WorkflowDefinitionRecord = {
  key: 'question_content',
  label: 'Question Content',
  nodes: [
    {
      key: 'extract',
      label: '提取',
      after: [],
      capability: 'extract',
      inputs: [],
      outputs: [],
    },
    {
      key: 'review',
      label: '审核',
      after: ['extract'],
      capability: 'review',
      inputs: [],
      outputs: [],
    },
  ],
  edges: [],
  intake: { modes: [] },
}

const defaultJobs: JobSummary[] = [
  createJobSummary({
    id: 'j1',
    status: 'running',
    active_node_key: 'extract',
    workflow_version: 3,
  }),
  createJobSummary({
    id: 'j2',
    status: 'completed',
    active_node_key: 'review',
    workflow_version: 3,
  }),
]

function renderBar(
  options: { jobs?: JobSummary[] } & Record<string, unknown> = {}
) {
  const { jobs, ...props } = options
  const onChange = vi.fn()
  const nextJobs = jobs ?? defaultJobs
  useJobStore.setState({
    jobs: nextJobs,
    optionAccumulator: createOptionAccumulator(nextJobs),
  })
  const utils = render(
    <JobFilterBar
      filterConfig={{
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
      }}
      counts={{
        status: { all: 2, pending: 0, running: 1, completed: 1, failed: 0 },
        workflowVersion: { all: 2, '3': 2 },
        activeNodeKey: { all: 2, extract: 1, review: 1 },
      }}
      workflowDefinition={workflowDefinition}
      onChange={onChange}
      {...props}
    />
  )
  return { ...utils, onChange }
}

describe('JobFilterBar', () => {
  it('renders all filter controls', () => {
    renderBar()
    expect(screen.getByLabelText('状态')).toBeInTheDocument()
    expect(screen.getByLabelText('Workflow 版本')).toBeInTheDocument()
    expect(screen.getByLabelText('当前运行节点')).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText('搜索 ID / 标题 / 批次')
    ).toBeInTheDocument()
  })

  it('shows cascade counts in status options', () => {
    renderBar()
    fireEvent.mouseDown(screen.getByLabelText('状态'))
    expect(
      screen.getByRole('option', { name: '全部状态 (2)' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('option', { name: '运行中 (1)' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('option', { name: '已完成 (1)' })
    ).toBeInTheDocument()
  })

  it('calls onChange when status changes', () => {
    const { onChange } = renderBar()
    fireEvent.mouseDown(screen.getByLabelText('状态'))
    fireEvent.click(screen.getByText('失败 (0)'))
    expect(onChange).toHaveBeenCalledWith({ status: 'failed' })
  })

  it('calls onChange with debounce when searching', () => {
    vi.useFakeTimers()
    const { onChange } = renderBar()
    const input = screen.getByPlaceholderText('搜索 ID / 标题 / 批次')
    fireEvent.change(input, { target: { value: 'algebra' } })
    expect(onChange).not.toHaveBeenCalled()
    act(() => {
      vi.advanceTimersByTime(250)
    })
    expect(onChange).toHaveBeenCalledWith({ search: 'algebra' })
    vi.useRealTimers()
  })

  it('shows missing version option when some jobs have no version', () => {
    renderBar({
      jobs: [
        createJobSummary({ id: 'j1', workflow_version: 3 }),
        createJobSummary({ id: 'j2' }),
      ],
      counts: {
        status: { all: 2, pending: 0, running: 1, completed: 1, failed: 0 },
        workflowVersion: { all: 2, '3': 1, none: 1 },
        activeNodeKey: { all: 2, extract: 1, review: 1 },
      },
    })
    fireEvent.mouseDown(screen.getByLabelText('Workflow 版本'))
    expect(
      screen.getByRole('option', { name: '未指定版本 (1)' })
    ).toBeInTheDocument()
  })

  it('calls onChange with none when missing version is selected', () => {
    const { onChange } = renderBar({
      jobs: [createJobSummary({ id: 'j1' })],
      counts: {
        status: { all: 1, pending: 1, running: 0, completed: 0, failed: 0 },
        workflowVersion: { all: 1, none: 1 },
        activeNodeKey: { all: 1 },
      },
    })
    fireEvent.mouseDown(screen.getByLabelText('Workflow 版本'))
    fireEvent.click(screen.getByText('未指定版本 (1)'))
    expect(onChange).toHaveBeenCalledWith({ workflowVersion: 'none' })
  })

  it('renders active filter chips', () => {
    renderBar({
      filterConfig: {
        status: 'failed',
        search: 'boom',
        workflowVersion: 3,
        activeNodeKey: 'review',
      },
    })
    expect(screen.getByText('状态: 失败')).toBeInTheDocument()
    expect(screen.getByText('版本: v3')).toBeInTheDocument()
    expect(screen.getByText('节点: 审核')).toBeInTheDocument()
    expect(screen.getByText('搜索: "boom"')).toBeInTheDocument()
  })

  it('renders missing version active filter chip', () => {
    renderBar({
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: 'none',
        activeNodeKey: null,
      },
      jobs: [createJobSummary({ id: 'j1' })],
      counts: {
        status: { all: 1, pending: 1, running: 0, completed: 0, failed: 0 },
        workflowVersion: { all: 1, none: 1 },
        activeNodeKey: { all: 1 },
      },
    })
    expect(screen.getByText('版本: 未指定版本')).toBeInTheDocument()
  })

  it('clears a filter when chip delete is clicked', () => {
    const onChange = vi.fn()
    renderBar({
      filterConfig: {
        status: 'failed',
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
      },
      onChange,
    })
    const chip = screen.getByText('状态: 失败').closest('.MuiChip-root')
    const deleteIcon = chip?.querySelector('[data-testid="CancelIcon"]')
    expect(deleteIcon).toBeTruthy()
    fireEvent.click(deleteIcon!)
    expect(onChange).toHaveBeenCalledWith({ status: null })
  })
})
