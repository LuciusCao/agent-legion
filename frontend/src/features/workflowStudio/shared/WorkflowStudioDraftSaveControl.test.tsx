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
  return render(
    <WorkflowStudioDraftSaveControl save={save} readOnly={readOnly} />
  )
}

describe('WorkflowStudioDraftSaveControl', () => {
  it('保存中显示瞬态文本', () => {
    renderControl({ status: 'saving', savedAt: null })
    expect(screen.getByText('草稿保存中…')).toBeInTheDocument()
  })

  it('保存失败显示将自动重试的警示', () => {
    renderControl({ status: 'error', savedAt: null })
    expect(screen.getByText('草稿保存失败，将自动重试')).toBeInTheDocument()
  })

  it('#804 定案：pending（未保存更改）不占位——debounce 窗口内静默', () => {
    const { container } = renderControl({ status: 'pending', savedAt: null })
    expect(container).toBeEmptyDOMElement()
  })

  it('#804 定案：保存成功即隐——不再有「已保存 HH:MM」与手动保存按钮', () => {
    const { container } = renderControl({
      status: 'saved',
      savedAt: '2026-08-27T09:05:00+00:00',
    })
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByRole('button', { name: '保存草稿' })).toBeNull()
  })

  it('shows the service-unavailable warning when the draft query failed', () => {
    renderControl({ status: 'idle', savedAt: null, loadError: true })
    expect(
      screen.getByText('草稿服务不可用，编辑仅保留在本页内存')
    ).toBeInTheDocument()
  })

  it('冲突态：警示常驻 + 显式二选一动作（采用 Agent 版本 / 保留本页编辑）', () => {
    const onAdoptServer = vi.fn()
    const onKeepMine = vi.fn()
    render(
      <WorkflowStudioDraftSaveControl
        save={{ status: 'idle', savedAt: null, conflict: true }}
        readOnly={false}
        onAdoptServer={onAdoptServer}
        onKeepMine={onKeepMine}
      />
    )
    expect(
      screen.getByText(/自动保存已暂停——请选择采用 Agent 版本或保留本页编辑/)
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '采用 Agent 版本' }))
    expect(onAdoptServer).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: '保留本页编辑' }))
    expect(onKeepMine).toHaveBeenCalledOnce()
  })

  it('codex 轮 3 P1：窄屏一刀切隐藏只盖瞬态文本，冲突簇（警示 + 操作出口）不挂窄屏隐藏类', () => {
    // jsdom 跑不了 @media——钉结构：冲突长文案挂 island 的 secondary 类
    // （窄屏隐藏），但冲突按钮与 ⚠ 图标不带该类（窄屏恒可见可操作）。
    // revert 即红：整组挂回 secondary/conditional 时按钮会被断言出携带。
    render(
      <WorkflowStudioDraftSaveControl
        save={{ status: 'idle', savedAt: null, conflict: true }}
        readOnly={false}
        onAdoptServer={vi.fn()}
        onKeepMine={vi.fn()}
      />
    )
    const adopt = screen.getByRole('button', { name: '采用 Agent 版本' })
    const keep = screen.getByRole('button', { name: '保留本页编辑' })
    for (const el of [adopt, keep]) {
      expect(el.closest('[class*="secondary"]')).toBeNull()
    }
    // 长文案带 secondary（窄屏让位给 ⚠ 图标）。
    expect(screen.getByText(/自动保存已暂停/).className).toContain('secondary')
  })

  it('只读态不提供冲突动作', () => {
    const { container } = render(
      <WorkflowStudioDraftSaveControl
        save={{ status: 'idle', savedAt: null, conflict: true }}
        readOnly
        onAdoptServer={vi.fn()}
        onKeepMine={vi.fn()}
      />
    )
    expect(screen.queryByRole('button')).toBeNull()
    // 警示文本仍可见。
    expect(container).toHaveTextContent(/自动保存已暂停/)
  })
})

describe('WorkflowStudioDraftSaveControlContainer', () => {
  it('wires the conflict resolution actions from studio state', () => {
    const resolveConflict = vi.fn()
    vi.mocked(useStudioState).mockReturnValue({
      draftSave: { status: 'idle', savedAt: null, conflict: true },
      readOnly: false,
      resolveConflict,
      adoptServerDraft: vi.fn(),
    } as unknown as ReturnType<typeof useStudioState>)

    render(<WorkflowStudioDraftSaveControlContainer />)
    fireEvent.click(screen.getByRole('button', { name: '保留本页编辑' }))

    expect(resolveConflict).toHaveBeenCalledWith(true)
  })
})
