import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { NodeDetailsPanel } from './NodeDetailsPanel'
import type { DagNodeData } from './DagNode'
import type { components } from '../generated/api'

const baseData: DagNodeData = {
  label: 'generate_summary',
  status: 'running',
  duration: 4.2,
  executorKind: 'local',
  inputs: ['keywords.json'],
  outputs: ['summary.json', 'report.md'],
}

const baseRun: components['schemas']['NodeRunResponse'] = {
  id: 42,
  status: 'running',
  started_at: '2026-06-17T13:45:02Z',
  exit_code: null,
  error_message: '',
  command_json: '{}',
  job_id: 'job-1',
  log_path: '/tmp/log',
  node_key: 'generate_summary',
  run_dir: '/tmp/run',
  session_dir: '/tmp/session',
}

describe('NodeDetailsPanel', () => {
  it('renders node details and log button', () => {
    render(
      <NodeDetailsPanel
        nodeKey="generate_summary"
        data={baseData}
        latestRun={baseRun}
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

  it('shows em dash when inputs and outputs are empty', () => {
    render(
      <NodeDetailsPanel
        nodeKey="generate_summary"
        data={{ ...baseData, inputs: [], outputs: [] }}
        latestRun={null}
        onViewLogs={vi.fn()}
      />
    )
    expect(screen.getAllByText('—')).toHaveLength(2)
  })

  it.each([
    ['pending', 'radio_button_unchecked', '等待中'],
    ['running', 'hourglass_empty', '运行中'],
    ['completed', 'check_circle', '已完成'],
    ['failed', 'error', '失败'],
    ['stale', 'warning', '需重跑'],
    ['ready', 'play_circle', '就绪'],
    ['not_applicable', 'block', '不适用'],
  ] as const)(
    'renders %s status with icon %s and label %s',
    (status, icon, label) => {
      render(
        <NodeDetailsPanel
          nodeKey="generate_summary"
          data={{ ...baseData, status }}
          latestRun={null}
          onViewLogs={vi.fn()}
        />
      )
      expect(screen.getByText(icon)).toBeInTheDocument()
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  )

  it('still shows log button when latestRun is absent', () => {
    render(
      <NodeDetailsPanel
        nodeKey="generate_summary"
        data={baseData}
        latestRun={null}
        onViewLogs={vi.fn()}
      />
    )
    const button = screen.getByText('查看日志')
    expect(button).toBeInTheDocument()
    expect(button).toHaveAttribute('type', 'button')
  })

  it('renders error_message when latestRun has one', () => {
    render(
      <NodeDetailsPanel
        nodeKey="generate_summary"
        data={{ ...baseData, status: 'failed' }}
        latestRun={{ ...baseRun, status: 'failed', error_message: 'disk full' }}
        onViewLogs={vi.fn()}
      />
    )
    expect(screen.getByText('disk full')).toBeInTheDocument()
  })
})
