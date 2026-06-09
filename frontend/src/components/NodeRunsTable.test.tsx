import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { NodeRunsTable } from './NodeRunsTable'
import styles from './NodeRunsTable.module.css'

describe('NodeRunsTable', () => {
  const runs = [
    {
      nodeKey: 'download',
      nodeLabel: '下载',
      status: 'completed',
      time: '10:00',
      duration: '12s',
    },
    {
      nodeKey: 'transcribe',
      nodeLabel: '转录',
      status: 'running',
      time: '10:05',
      duration: '-',
    },
  ]

  it('renders header columns', () => {
    render(<NodeRunsTable runs={runs} />)

    expect(screen.getByText('节点')).toBeInTheDocument()
    expect(screen.getByText('状态')).toBeInTheDocument()
    expect(screen.getByText('时间')).toBeInTheDocument()
    expect(screen.getByText('耗时')).toBeInTheDocument()
  })

  it('renders each run row', () => {
    render(<NodeRunsTable runs={runs} />)

    expect(screen.getByText('下载')).toBeInTheDocument()
    expect(screen.getByText('转录')).toBeInTheDocument()
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('10:00')).toBeInTheDocument()
    expect(screen.getByText('12s')).toBeInTheDocument()
  })

  it('has styled header row', () => {
    const { container } = render(<NodeRunsTable runs={runs} />)
    const header = container.querySelector('thead tr')
    expect(header).toHaveClass(styles.headerRow)
  })

  it('renders empty body without errors', () => {
    const { container } = render(<NodeRunsTable runs={[]} />)
    expect(container.querySelectorAll('tbody tr')).toHaveLength(0)
  })
})
