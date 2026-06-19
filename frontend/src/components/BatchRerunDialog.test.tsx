import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { BatchRerunDialog } from './BatchRerunDialog'
import { PHASE_LABELS } from '../labels'

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
    render(
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
      screen.getByRole('button', { name: PHASE_LABELS.download })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: PHASE_LABELS.transcribe })
    ).toBeInTheDocument()
    expect(screen.getByText('K001')).toBeInTheDocument()
    expect(screen.getByText('K002')).toBeInTheDocument()
  })

  it('marks non-rerunnable videos when selecting a later phase', () => {
    render(
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
    const assembleChip = screen.getByRole('button', {
      name: PHASE_LABELS.assemble,
    })
    expect(assembleChip).toBeInTheDocument()
    act(() => {
      assembleChip.click()
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
      screen.getByRole('button', { name: /重跑 \d+ 个视频/ }).click()
    })

    expect(onConfirm).toHaveBeenCalledWith(['v1', 'v2'], 'download')
    expect(onClose).toHaveBeenCalled()
  })

  it('renders failed-phase chip and filters runnable videos', () => {
    render(
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
    const failedChip = screen.getByRole('button', {
      name: PHASE_LABELS.__failed__,
    })
    expect(failedChip).toBeInTheDocument()
    act(() => {
      failedChip.click()
    })

    // v1 is completed, v2 is failed — only v2 should be runnable
    expect(screen.getByText('未失败，跳过')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /重跑 1 个视频/ })
    ).toBeInTheDocument()
  })

  it('calls onConfirm with __failed__ phase when failed-phase chip selected', async () => {
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

    const failedChip = screen.getByRole('button', {
      name: PHASE_LABELS.__failed__,
    })
    act(() => {
      failedChip.click()
    })

    await act(async () => {
      screen.getByRole('button', { name: /重跑 1 个视频/ }).click()
    })

    expect(onConfirm).toHaveBeenCalledWith(['v1', 'v2'], '__failed__')
    expect(onClose).toHaveBeenCalled()
  })
})
