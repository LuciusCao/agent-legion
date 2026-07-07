import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { JobStatusFilterPanel } from './JobStatusFilterPanel'
import styles from './JobStatusFilterPanel.module.css'

const COUNTS = {
  all: 10,
  pending: 2,
  running: 3,
  completed: 4,
  failed: 1,
  paused: 0,
}

function renderPanel(props = {}) {
  const onChange = vi.fn()
  const utils = render(
    <JobStatusFilterPanel
      value={null}
      counts={COUNTS}
      onChange={onChange}
      {...props}
    />
  )
  return { ...utils, onChange }
}

describe('JobStatusFilterPanel', () => {
  it('renders all status options including all', () => {
    renderPanel()
    expect(screen.getByRole('button', { name: '全部 (10)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '等待中 (2)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '运行中 (3)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '已完成 (4)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '失败 (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '已暂停 (0)' })).toBeInTheDocument()
  })

  it('marks the selected status as active', () => {
    renderPanel({ value: 'running' })
    const active = screen.getByRole('button', { name: '运行中 (3)' })
    expect(active).toHaveClass(styles.active)
  })

  it('calls onChange with the selected status', () => {
    const { onChange } = renderPanel()
    fireEvent.click(screen.getByRole('button', { name: '失败 (1)' }))
    expect(onChange).toHaveBeenCalledWith('failed')
  })

  it('calls onChange with null when all is selected', () => {
    const { onChange } = renderPanel({ value: 'running' })
    fireEvent.click(screen.getByRole('button', { name: '全部 (10)' }))
    expect(onChange).toHaveBeenCalledWith(null)
  })

  it('falls back to 0 when a count is missing', () => {
    renderPanel({ counts: { all: 1 } })
    expect(screen.getByRole('button', { name: '等待中 (0)' })).toBeInTheDocument()
  })
})
