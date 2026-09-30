import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { WorkflowStudioCommandBarActions } from './WorkflowStudioCommandBarActions'

function renderActions(overrides: Record<string, unknown> = {}) {
  const props = {
    readOnly: false,
    dirty: false,
    actionState: 'idle' as const,
    canPublish: true,
    onPublish: vi.fn(),
    onReset: vi.fn(),
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

it('labels runtime-only changes as a save without a new version', () => {
  renderActions({ dirty: true, createsRevision: false })
  expect(
    screen.getByRole('button', { name: '保存运行配置' })
  ).toBeInTheDocument()
})

it('草稿态动作组（#804 定案）：发布主按钮 + 仅 dirty 外露的重置；无校验按钮、无 ⋮ 菜单', () => {
  const props = renderActions({ dirty: true })

  // 发布保持 contained 文字主按钮（#804 文案：发布新版本 → 发布）。
  expect(screen.getByRole('button', { name: '发布' })).toBeInTheDocument()
  // 重置外露为 outlined 次级按钮（dirty 时），不再是 ⋮ 菜单项。
  const reset = screen.getByRole('button', { name: '重置' })
  expect(reset).toHaveClass('MuiButton-outlined')
  fireEvent.click(reset)
  expect(props.onReset).toHaveBeenCalledOnce()
  // 校验按钮退役（自动校验取代）；单一项的 ⋮ 溢出菜单退役。
  expect(screen.queryByRole('button', { name: '校验' })).toBeNull()
  expect(screen.queryByRole('button', { name: '更多操作' })).toBeNull()
})

it('干净态不渲染重置按钮（仅 dirty 时外露）', () => {
  renderActions({ dirty: false })
  expect(screen.queryByRole('button', { name: '重置' })).toBeNull()
  expect(screen.getByRole('button', { name: '发布' })).toBeInTheDocument()
})

it('canPublish=false 时发布禁用，publishTooltip 说明原因（codex 轮 3 P2：门控含当前 YAML 校验通过）', () => {
  renderActions({
    dirty: true,
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
  renderActions({ dirty: true })
  expect(screen.getByRole('button', { name: '发布' })).toBeEnabled()
})
