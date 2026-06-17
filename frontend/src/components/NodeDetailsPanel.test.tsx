import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { NodeDetailsPanel } from './NodeDetailsPanel'
import type { DagNodeData } from './DagNode'

const baseData: DagNodeData = {
  label: 'generate_summary',
  status: 'running',
  duration: 4.2,
  executorKind: 'local',
  inputs: ['keywords.json'],
  outputs: ['summary.json', 'report.md'],
}

describe('NodeDetailsPanel', () => {
  it('renders node details and log button', () => {
    render(
      <NodeDetailsPanel
        nodeKey="generate_summary"
        data={baseData}
        latestRun={{
          id: 42,
          status: 'running',
          started_at: '2026-06-17T13:45:02Z',
          exit_code: null,
        }}
        onViewLogs={vi.fn()}
      />
    )
    expect(screen.getByText('generate_summary')).toBeInTheDocument()
    expect(screen.getByText('local')).toBeInTheDocument()
    expect(screen.getByText('keywords.json')).toBeInTheDocument()
    expect(screen.getByText('summary.json')).toBeInTheDocument()
    expect(screen.getByText('查看日志')).toBeInTheDocument()
  })

  it('calls onViewLogs when log button clicked', () => {
    const onViewLogs = vi.fn()
    render(
      <NodeDetailsPanel
        nodeKey="generate_summary"
        data={baseData}
        latestRun={null}
        onViewLogs={onViewLogs}
      />
    )
    fireEvent.click(screen.getByText('查看日志'))
    expect(onViewLogs).toHaveBeenCalledWith('generate_summary')
  })
})
