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

it('草稿态动作组新形态（#799 精修）：校验图标按钮 + 发布主按钮 + 重置收进 ⋮ 菜单', () => {
  const onValidate = vi.fn()
  const onReset = vi.fn()
  render(
    <WorkflowStudioCommandBarActions
      readOnly={false}
      dirty
      actionState="idle"
      canSubmit
      canPublish
      onValidate={onValidate}
      onPublish={vi.fn()}
      onReset={onReset}
      backToDraft={vi.fn()}
      useViewedRevisionAsDraft={vi.fn()}
    />
  )

  // 校验是图标按钮（aria-label + tooltip 承载文案）。
  fireEvent.click(screen.getByRole('button', { name: '校验' }))
  expect(onValidate).toHaveBeenCalledOnce()
  // 发布保持 contained 文字主按钮。
  expect(screen.getByRole('button', { name: '发布新版本' })).toBeInTheDocument()
  // 重置收进溢出菜单，菜单打开前不出现。
  expect(screen.queryByRole('menuitem', { name: '重置' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '更多操作' }))
  fireEvent.click(screen.getByRole('menuitem', { name: '重置' }))
  expect(onReset).toHaveBeenCalledOnce()
})
