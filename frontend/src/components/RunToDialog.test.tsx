import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { VideoItem } from '../types'
import { RunToDialog } from './RunToDialog'

function video(overrides: Partial<VideoItem> = {}): VideoItem {
  return {
    id: 'v1',
    title: 'Video 1',
    source_url: '',
    content_type: 'knowledge',
    external_id: 'K001',
    knowledge_code: 'K001',
    question_id: '',
    source_uuid: '',
    status: 'queued',
    current_phase: 'subtitle_review',
    error_message: '',
    storage_dir: '',
    duration: 0,
    packed: false,
    ...overrides,
  }
}

describe('RunToDialog', () => {
  it('renders continue mode and submits default target phase', async () => {
    const onConfirm = vi.fn()

    render(
      <RunToDialog
        open
        videos={[video()]}
        onClose={() => {}}
        onConfirm={onConfirm}
      />
    )

    expect(screen.getByText('运行到阶段')).toBeInTheDocument()

    await act(async () => {
      screen.getByText('运行到章节生成').click()
    })

    expect(onConfirm).toHaveBeenCalledWith({
      targetPhase: 'chapter_generate',
      startPhase: null,
    })
  })

  it('supports rerun mode with start and target phases', async () => {
    const onConfirm = vi.fn()
    const { container } = render(
      <RunToDialog
        open
        videos={[video()]}
        onClose={() => {}}
        onConfirm={onConfirm}
      />
    )

    const rerunChip = container.querySelector(
      'md-filter-chip[label="重跑并运行到"]'
    )
    expect(rerunChip).toBeInTheDocument()

    act(() => {
      ;(rerunChip as HTMLElement).click()
    })

    const transcribeChip = container.querySelector(
      'md-filter-chip[label="转录"]'
    )
    expect(transcribeChip).toBeInTheDocument()
    act(() => {
      ;(transcribeChip as HTMLElement).click()
    })

    await act(async () => {
      screen.getByText('从转录重跑到章节生成').click()
    })

    expect(onConfirm).toHaveBeenCalledWith({
      targetPhase: 'chapter_generate',
      startPhase: 'transcribe',
    })
  })

  it('shows ineligible selected videos for running item', () => {
    render(
      <RunToDialog
        open
        videos={[video({ status: 'running', current_phase: 'transcribe' })]}
        onClose={() => {}}
        onConfirm={() => {}}
      />
    )

    expect(screen.getByText('正在处理中')).toBeInTheDocument()
    expect(screen.getByText('运行到章节生成')).toBeDisabled()
  })
})
