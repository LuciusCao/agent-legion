import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { WorkflowStudioCommandBarActions } from './WorkflowStudioCommandBarActions'

it('uses explicit Chinese labels for historical revision actions', () => {
  const backToDraft = vi.fn()
  const useViewedRevisionAsDraft = vi.fn()
  render(
    <WorkflowStudioCommandBarActions
      readOnly
      dirty={false}
      actionState="idle"
      canSubmit={false}
      canPublish={false}
      onValidate={vi.fn()}
      onPublish={vi.fn()}
      onReset={vi.fn()}
      backToDraft={backToDraft}
      useViewedRevisionAsDraft={useViewedRevisionAsDraft}
    />
  )

  fireEvent.click(screen.getByRole('button', { name: '返回' }))
  fireEvent.click(screen.getByRole('button', { name: '设为草稿' }))

  expect(backToDraft).toHaveBeenCalledOnce()
  expect(useViewedRevisionAsDraft).toHaveBeenCalledOnce()
})

it('labels runtime-only changes as a save without a new version', () => {
  render(
    <WorkflowStudioCommandBarActions
      readOnly={false}
      dirty
      actionState="idle"
      canSubmit
      canPublish
      createsRevision={false}
      onValidate={vi.fn()}
      onPublish={vi.fn()}
      onReset={vi.fn()}
      backToDraft={vi.fn()}
      useViewedRevisionAsDraft={vi.fn()}
    />
  )

  expect(
    screen.getByRole('button', { name: '保存运行配置' })
  ).toBeInTheDocument()
})
