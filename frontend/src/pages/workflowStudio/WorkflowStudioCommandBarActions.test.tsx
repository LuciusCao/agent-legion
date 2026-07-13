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

  fireEvent.click(screen.getByRole('button', { name: '返回当前草稿' }))
  fireEvent.click(screen.getByRole('button', { name: '用此版本替换草稿' }))

  expect(backToDraft).toHaveBeenCalledOnce()
  expect(useViewedRevisionAsDraft).toHaveBeenCalledOnce()
})
