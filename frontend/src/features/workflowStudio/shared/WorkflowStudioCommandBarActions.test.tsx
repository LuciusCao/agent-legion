import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { WorkflowStudioCommandBarActions } from './WorkflowStudioCommandBarActions'

function renderActions(overrides: Record<string, unknown> = {}) {
  const props = {
    readOnly: false,
    publishing: false,
    validating: false,
    canPublish: true,
    onPublish: vi.fn(),
    backToDraft: vi.fn(),
    useViewedRevisionAsDraft: vi.fn(),
    ...overrides,
  }
  render(<WorkflowStudioCommandBarActions {...props} />)
  return props
}

it('uses explicit Chinese labels for historical revision actions', () => {
  const props = renderActions({
    readOnly: true,
    dirty: false,
    canPublish: false,
  })

  fireEvent.click(screen.getByRole('button', { name: '返回' }))
  fireEvent.click(screen.getByRole('button', { name: '设为草稿' }))

  expect(props.backToDraft).toHaveBeenCalledOnce()
  expect(props.useViewedRevisionAsDraft).toHaveBeenCalledOnce()
})

it('轮 4 P2-E：只读态「返回」用 text 变体（窄屏 outlined 隐藏规则误伤不到非破坏出口）', () => {
  renderActions({ readOnly: true, canPublish: false })
  const back = screen.getByRole('button', { name: '返回' })
  expect(back).toHaveClass('MuiButton-text')
})

it('轮 4 P2-E：「设为草稿」在草稿有变更时需确认（confirmAdoptDraft）', () => {
  const props = renderActions({
    readOnly: true,
    canPublish: false,
    confirmAdoptDraft: true,
  })
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
  fireEvent.click(screen.getByRole('button', { name: '设为草稿' }))
  expect(confirmSpy).toHaveBeenCalledOnce()
  // 取消 → 不覆盖草稿。
  expect(props.useViewedRevisionAsDraft).not.toHaveBeenCalled()

  confirmSpy.mockReturnValue(true)
  fireEvent.click(screen.getByRole('button', { name: '设为草稿' }))
  expect(props.useViewedRevisionAsDraft).toHaveBeenCalledOnce()
  confirmSpy.mockRestore()
})

it('轮 4 P2-E：草稿干净时「设为草稿」无需确认', () => {
  const props = renderActions({
    readOnly: true,
    canPublish: false,
    confirmAdoptDraft: false,
  })
  const confirmSpy = vi.spyOn(window, 'confirm')
  fireEvent.click(screen.getByRole('button', { name: '设为草稿' }))
  expect(confirmSpy).not.toHaveBeenCalled()
  expect(props.useViewedRevisionAsDraft).toHaveBeenCalledOnce()
  confirmSpy.mockRestore()
})

it('labels runtime-only changes as a save without a new version', () => {
  renderActions({ createsRevision: false })
  expect(
    screen.getByRole('button', { name: '保存运行配置' })
  ).toBeInTheDocument()
})

it('草稿态动作组（#770 顶栏减法）：只剩发布主按钮；重置不再外露（收进版本菜单）、无校验按钮、无 ⋮ 菜单', () => {
  renderActions({ createsRevision: true })

  expect(screen.getByRole('button', { name: '发布' })).toHaveClass(
    'MuiButton-contained'
  )
  expect(screen.queryByRole('button', { name: '重置' })).toBeNull()
  expect(screen.queryByRole('button', { name: '校验' })).toBeNull()
  expect(screen.queryByRole('button', { name: '更多操作' })).toBeNull()
})

it('canPublish=false 时发布禁用，publishTooltip 说明原因（codex 轮 3 P2：门控含当前 YAML 校验通过）', () => {
  renderActions({
    canPublish: false,
    publishTooltip: '校验失败，请修复后重新发布',
  })
  const publish = screen.getByRole('button', { name: '发布' })
  expect(publish).toBeDisabled()
  // MUI Tooltip 把 title 克隆为 wrapper span 的 aria-label。
  expect(publish.parentElement).toHaveAttribute(
    'aria-label',
    '校验失败，请修复后重新发布'
  )
})

it('canPublish=true 时发布可用', () => {
  renderActions()
  expect(screen.getByRole('button', { name: '发布' })).toBeEnabled()
})
