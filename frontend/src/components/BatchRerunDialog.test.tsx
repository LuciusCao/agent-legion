import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { BatchRerunDialog } from './BatchRerunDialog'

const phases = [
  'download',
  'transcribe',
  'subtitle_review',
  'chapter_generate',
  'interaction_generate',
  'content_review',
  'assemble',
]

const items = [
  {
    id: 'v1',
    name: 'K001',
    currentPhase: 'package',
    status: 'completed',
  },
  {
    id: 'v2',
    name: 'K002',
    currentPhase: 'subtitle_review',
    status: 'failed',
  },
]

describe('BatchRerunDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders chips and video list', () => {
    const { container } = render(
      <BatchRerunDialog
        open
        items={items}
        phases={phases}
        itemLabel="视频"
        onConfirm={vi.fn()}
        onClose={() => {}}
      />
    )

    expect(screen.getByText('选择重跑阶段')).toBeInTheDocument()
    expect(
      container.querySelector('md-filter-chip[label="下载"]')
    ).toBeInTheDocument()
    expect(
      container.querySelector('md-filter-chip[label="转录"]')
    ).toBeInTheDocument()
    expect(screen.getByText('K001')).toBeInTheDocument()
    expect(screen.getByText('K002')).toBeInTheDocument()
  })

  it('marks non-rerunnable videos when selecting a later phase', () => {
    const { container } = render(
      <BatchRerunDialog
        open
        items={items}
        phases={phases}
        itemLabel="视频"
        onConfirm={vi.fn()}
        onClose={() => {}}
      />
    )

    // By default "download" is selected, both videos can rerun
    expect(screen.queryByText(/无法重跑/)).not.toBeInTheDocument()

    // Click "assemble" chip — v2 at subtitle_review cannot rerun from assemble
    const assembleChip = container.querySelector('md-filter-chip[label="组装"]')
    expect(assembleChip).toBeInTheDocument()
    act(() => {
      ;(assembleChip as HTMLElement).click()
    })

    expect(screen.getByText(/当前处于 字幕审核/)).toBeInTheDocument()
  })

  it('calls onConfirm on confirm', async () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(
      <BatchRerunDialog
        open
        items={items}
        phases={phases}
        itemLabel="视频"
        onConfirm={onConfirm}
        onClose={onClose}
      />
    )

    await act(async () => {
      screen.getByText('重跑 2 个视频').click()
    })

    expect(onConfirm).toHaveBeenCalledWith(['v1', 'v2'], 'download')
    expect(onClose).toHaveBeenCalled()
  })

  it('renders failed-phase chip and filters runnable videos', () => {
    const { container } = render(
      <BatchRerunDialog
        open
        items={items}
        phases={phases}
        itemLabel="视频"
        onConfirm={vi.fn()}
        onClose={() => {}}
      />
    )

    // Click "失败的阶段" chip
    const failedChip = container.querySelector(
      'md-filter-chip[label="失败的阶段"]'
    )
    expect(failedChip).toBeInTheDocument()
    act(() => {
      ;(failedChip as HTMLElement).click()
    })

    // v1 is completed, v2 is failed — only v2 should be runnable
    expect(screen.getByText('未失败，跳过')).toBeInTheDocument()
    expect(screen.getByText('重跑 1 个视频')).toBeInTheDocument()
  })

  it('calls onConfirm with __failed__ phase when failed-phase chip selected', async () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    const { container } = render(
      <BatchRerunDialog
        open
        items={items}
        phases={phases}
        itemLabel="视频"
        onConfirm={onConfirm}
        onClose={onClose}
      />
    )

    const failedChip = container.querySelector(
      'md-filter-chip[label="失败的阶段"]'
    )
    act(() => {
      ;(failedChip as HTMLElement).click()
    })

    await act(async () => {
      screen.getByText('重跑 1 个视频').click()
    })

    expect(onConfirm).toHaveBeenCalledWith(['v1', 'v2'], '__failed__')
    expect(onClose).toHaveBeenCalled()
  })
})
