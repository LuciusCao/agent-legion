import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'
import { WorkflowNodeRuntimeSaveBar } from './WorkflowNodeRuntimeSaveBar'

function executionChange(nodeKey = 'draft', fields = ['execution']) {
  return {
    type: 'modified',
    nodeKey,
    label: nodeKey,
    nodeType: 'agent',
    fields,
    severity: 'info',
  }
}

function makeStudio(overrides: Record<string, unknown> = {}) {
  return {
    draftSave: { status: 'saved', savedAt: null },
    flushDraftSave: vi.fn().mockResolvedValue({ ok: true }),
    requestPublish: vi.fn(),
    canPublish: true,
    validating: false,
    publishing: false,
    compareSummary: null,
    ...overrides,
  }
}

function renderBar(
  studioOverrides: Record<string, unknown> = {},
  props: { readOnly?: boolean } = {}
) {
  const studio = makeStudio(studioOverrides)
  render(
    withStudioProviders(
      studio,
      makeStudioView(),
      <WorkflowNodeRuntimeSaveBar nodeKey="draft" {...props} />
    )
  )
  return studio
}

describe('WorkflowNodeRuntimeSaveBar（#769 execution 面板内保存）', () => {
  it('有未保存修改时「保存草稿」可用，点击立即 flush 整份草稿通道（不等 debounce）', () => {
    const studio = renderBar({
      draftSave: { status: 'pending', savedAt: null },
    })
    expect(screen.getByRole('status')).toHaveTextContent('有未保存的修改')
    const save = screen.getByRole('button', { name: '保存草稿' })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    expect(studio.flushDraftSave).toHaveBeenCalledOnce()
  })

  it('已保存：面板内常驻「已保存到草稿」，保存按钮禁用', () => {
    renderBar({ draftSave: { status: 'saved', savedAt: null } })
    expect(screen.getByRole('status')).toHaveTextContent('已保存到草稿')
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled()
  })

  it('codex P2：无 savedAt 的 idle（服务端草稿查询未完成等）不宣称已保存', () => {
    renderBar({ draftSave: { status: 'idle', savedAt: null } })
    expect(screen.getByRole('status')).toHaveTextContent('草稿尚未保存')
    expect(screen.getByRole('status')).not.toHaveTextContent('已保存')
  })

  it('hydrate 后的 idle（带服务端 savedAt、无待存编辑）显示已保存', () => {
    renderBar({
      draftSave: { status: 'idle', savedAt: '2026-10-04T01:30:00Z' },
    })
    expect(screen.getByRole('status')).toHaveTextContent('已保存到草稿 ·')
  })

  it('保存失败（重试耗尽）可在面板内重试', () => {
    const studio = renderBar({ draftSave: { status: 'error', savedAt: null } })
    expect(screen.getByRole('status')).toHaveTextContent('保存失败')
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
    expect(studio.flushDraftSave).toHaveBeenCalledOnce()
  })

  it('#633 冲突态：面板保存禁用（不做隐式 keep-mine），提示去顶部警示二选一', () => {
    renderBar({
      draftSave: { status: 'pending', savedAt: null, conflict: true },
      compareSummary: {
        createsRevision: false,
        nodeChanges: [executionChange()],
      },
    })
    expect(screen.getByRole('status')).toHaveTextContent('草稿冲突')
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '应用到运行' })).toBeDisabled()
  })

  it('全画布仅 execution 改动：「应用到运行」可用，走顶栏同一发布确认框', () => {
    const studio = renderBar({
      compareSummary: {
        createsRevision: false,
        nodeChanges: [executionChange()],
      },
    })
    expect(screen.getByText(/不产生新版本/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '应用到运行' }))
    expect(studio.requestPublish).toHaveBeenCalledOnce()
  })

  it('画布另有结构改动：「应用到运行」禁用并说明——面板动作不会连带发布结构改动', () => {
    const studio = renderBar({
      compareSummary: {
        createsRevision: true,
        nodeChanges: [executionChange(), executionChange('other', ['inputs'])],
      },
    })
    const apply = screen.getByRole('button', { name: '应用到运行' })
    expect(apply).toBeDisabled()
    expect(apply.parentElement).toHaveAttribute(
      'aria-label',
      expect.stringContaining('结构改动')
    )
    expect(screen.getByText(/草稿另含结构改动/)).toBeInTheDocument()
    expect(studio.requestPublish).not.toHaveBeenCalled()
  })

  it('本节点 execution 未改动时不出「应用到运行」', () => {
    renderBar({
      compareSummary: {
        createsRevision: false,
        nodeChanges: [executionChange('other')],
      },
    })
    expect(screen.queryByRole('button', { name: '应用到运行' })).toBeNull()
  })

  it('校验未通过：「应用到运行」禁用', () => {
    renderBar({
      canPublish: false,
      compareSummary: {
        createsRevision: false,
        nodeChanges: [executionChange()],
      },
    })
    expect(screen.getByRole('button', { name: '应用到运行' })).toBeDisabled()
  })

  it('只读查看或脱离 Studio Provider 时不渲染', () => {
    renderBar({}, { readOnly: true })
    expect(screen.queryByLabelText('运行配置保存')).toBeNull()
    render(<WorkflowNodeRuntimeSaveBar nodeKey="draft" />)
    expect(screen.queryByLabelText('运行配置保存')).toBeNull()
  })
})
