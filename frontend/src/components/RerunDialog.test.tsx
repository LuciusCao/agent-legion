import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { RerunDialog } from './RerunDialog'
import { useUiStore } from '../stores/uiStore'
import { makeVideo } from '../testing/fixtures'
import type { VideoItem } from '../types'

function renderOpen(video: VideoItem | null = null, onConfirm = vi.fn()) {
  useUiStore.setState({ rerunDialogOpen: true })
  return render(<RerunDialog video={video} onConfirm={onConfirm} />)
}

describe('RerunDialog', () => {
  beforeEach(() => {
    useUiStore.setState({ rerunDialogOpen: false })
  })

  it('renders when open', () => {
    const { container } = renderOpen(makeVideo())
    expect(screen.getByText('选择重跑阶段')).toBeInTheDocument()
    expect(container.querySelectorAll('md-list-item').length).toBeGreaterThan(0)
  })

  it('displays all knowledge phases for a completed knowledge video', () => {
    const { container } = renderOpen(
      makeVideo({
        content_type: 'knowledge',
        status: 'completed',
        current_phase: 'package',
      })
    )
    expect(container.querySelectorAll('md-list-item')).toHaveLength(7)
  })

  it('displays question phases for a completed question video', () => {
    const { container } = renderOpen(
      makeVideo({
        content_type: 'question',
        status: 'completed',
        current_phase: 'package',
      })
    )
    expect(container.querySelectorAll('md-list-item')).toHaveLength(5)
  })

  it('calls onConfirm with the selected phase and closes the dialog', () => {
    const onConfirm = vi.fn()
    renderOpen(
      makeVideo({
        content_type: 'knowledge',
        status: 'running',
        current_phase: 'transcribe',
      }),
      onConfirm
    )

    fireEvent.click(screen.getByText('确认'))

    expect(onConfirm).toHaveBeenCalledWith('download')
    expect(useUiStore.getState().rerunDialogOpen).toBe(false)
  })

  it('closes the dialog when cancelled', () => {
    renderOpen(makeVideo())

    fireEvent.click(screen.getByText('取消'))

    expect(useUiStore.getState().rerunDialogOpen).toBe(false)
  })
})
