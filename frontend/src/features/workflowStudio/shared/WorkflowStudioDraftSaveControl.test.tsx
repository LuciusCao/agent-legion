import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useStudioState } from './studioStateContext'
import {
  WorkflowStudioDraftSaveControl,
  WorkflowStudioDraftSaveControlContainer,
} from './WorkflowStudioDraftSaveControl'
import type { DraftSaveState } from './useWorkflowDraftPersistence'

vi.mock('./studioStateContext', () => ({ useStudioState: vi.fn() }))

function renderControl(save: DraftSaveState | undefined, readOnly = false) {
  const onSaveDraft = vi.fn()
  render(
    <WorkflowStudioDraftSaveControl
      save={save}
      readOnly={readOnly}
      onSaveDraft={onSaveDraft}
    />
  )
  return onSaveDraft
}

describe('WorkflowStudioDraftSaveControl', () => {
  it('shows 未保存更改 with an enabled save button while edits are pending', () => {
    const onSaveDraft = renderControl({ status: 'pending', savedAt: null })

    expect(screen.getByText('草稿有未保存更改')).toBeInTheDocument()
    const button = screen.getByRole('button', { name: '保存草稿' })
    expect(button).toBeEnabled()
    fireEvent.click(button)
    expect(onSaveDraft).toHaveBeenCalledTimes(1)
  })

  it('shows 保存中 and disables the button while saving', () => {
    renderControl({ status: 'saving', savedAt: null })

    expect(screen.getByText('草稿保存中…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled()
  })

  it('shows the saved-at time and disables the button once saved', () => {
    const savedAt = '2026-08-27T09:05:00+00:00'
    renderControl({ status: 'saved', savedAt })
    const at = new Date(savedAt)
    const hh = String(at.getHours()).padStart(2, '0')
    const mm = String(at.getMinutes()).padStart(2, '0')

    expect(screen.getByText(`草稿已保存 ${hh}:${mm}`)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled()
  })

  it('shows the failure warning and keeps the button enabled for manual retry', () => {
    renderControl({ status: 'error', savedAt: null })

    expect(screen.getByText('草稿保存失败，将自动重试')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeEnabled()
  })

  it('shows the service-unavailable warning when the draft query failed', () => {
    renderControl({ status: 'idle', savedAt: null, loadError: true })

    expect(
      screen.getByText('草稿服务不可用，编辑仅保留在本页内存')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled()
  })

  it('hides the save button in read-only mode but keeps the status text', () => {
    renderControl({ status: 'pending', savedAt: null }, true)

    expect(screen.getByText('草稿有未保存更改')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '保存草稿' })
    ).not.toBeInTheDocument()
  })
})

describe('WorkflowStudioDraftSaveControlContainer', () => {
  it('wires the studio draft save state and flush action', () => {
    const flushDraftSave = vi.fn()
    vi.mocked(useStudioState).mockReturnValue({
      draftSave: { status: 'pending', savedAt: null },
      readOnly: false,
      flushDraftSave,
    } as unknown as ReturnType<typeof useStudioState>)

    render(<WorkflowStudioDraftSaveControlContainer />)
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    expect(flushDraftSave).toHaveBeenCalledTimes(1)
  })
})
