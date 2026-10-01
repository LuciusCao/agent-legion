import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WorkflowStudioNarrowAlertBadge } from './WorkflowStudioNarrowAlertBadge'
import { makeStudioView, withStudioProviders } from './testStudioProviders'

function renderBadge(draftSave: Record<string, unknown>) {
  const view = makeStudioView()
  render(
    withStudioProviders({ draftSave }, view, <WorkflowStudioNarrowAlertBadge />)
  )
  return view
}

describe('WorkflowStudioNarrowAlertBadge（#804 轮 4 P1-B）', () => {
  it('冲突态显示徽标，点击切回画布页签', () => {
    const view = renderBadge({ status: 'error', savedAt: null, conflict: true })
    fireEvent.click(
      screen.getByRole('button', { name: '草稿冲突待处理，点击查看' })
    )
    expect(view.setMobilePanel).toHaveBeenCalledWith('graph')
  })

  it('保存终态失败 / 服务不可用同样出徽标', () => {
    renderBadge({ status: 'error', savedAt: null })
    expect(
      screen.getByRole('button', { name: '草稿保存失败，点击查看' })
    ).toBeInTheDocument()
  })

  it('loadError（草稿服务不可用）出徽标', () => {
    renderBadge({ status: 'idle', savedAt: null, loadError: true })
    expect(
      screen.getByRole('button', { name: '草稿服务不可用，点击查看' })
    ).toBeInTheDocument()
  })

  it('正常/瞬态（saving/pending/saved）不渲染', () => {
    for (const status of ['idle', 'saving', 'pending', 'saved'] as const) {
      const { unmount } = render(
        withStudioProviders(
          { draftSave: { status, savedAt: null } },
          makeStudioView(),
          <WorkflowStudioNarrowAlertBadge />
        )
      )
      expect(screen.queryByRole('button')).toBeNull()
      unmount()
    }
  })
})
