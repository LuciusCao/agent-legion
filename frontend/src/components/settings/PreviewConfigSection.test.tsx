import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PreviewConfigSection } from './PreviewConfigSection'
import { defaultSettings } from '../../stores/setting/state'
import type { WorkflowDefinitionRecord, WorkspaceSettings } from '../../types'

function makeWorkflow(
  nodes: Array<{ key: string; label: string; outputs: string[] }>
): WorkflowDefinitionRecord {
  return {
    key: 'wf',
    label: 'Demo',
    nodes: nodes.map((node, idx) => ({
      key: node.key,
      label: node.label,
      capability: node.key,
      after: [],
      inputs: [],
      outputs: node.outputs,
      id: idx + 1,
    })),
    edges: [],
    intake: {
      modes: [],
    },
  } as unknown as WorkflowDefinitionRecord
}

function renderSection(
  settings: WorkspaceSettings,
  workflowDefinition: WorkflowDefinitionRecord | null
) {
  const setSettings = vi.fn()
  render(
    <PreviewConfigSection
      settings={settings}
      workflowDefinition={workflowDefinition}
      setSettings={setSettings}
    />
  )
  return { setSettings }
}

describe('PreviewConfigSection', () => {
  it('按节点分组渲染声明产物', () => {
    renderSection(
      defaultSettings,
      makeWorkflow([
        { key: 'parse', label: '解析', outputs: ['questions.json'] },
        { key: 'assemble', label: '组装', outputs: ['comprehension_info.json'] },
      ])
    )

    expect(screen.getByText('解析')).toBeInTheDocument()
    expect(screen.getByText('组装')).toBeInTheDocument()
    expect(screen.getByText('questions.json')).toBeInTheDocument()
    expect(screen.getByText('comprehension_info.json')).toBeInTheDocument()
  })

  it('默认全部勾选（previewHidden 空）', () => {
    renderSection(
      defaultSettings,
      makeWorkflow([{ key: 'parse', label: '解析', outputs: ['questions.json'] }])
    )

    const checkbox = screen.getByRole('checkbox', { name: '' }) as HTMLInputElement
    expect(checkbox.checked).toBe(true)
  })

  it('隐藏列表中的产物不勾选；点击勾选从隐藏列表移除', () => {
    const { setSettings } = renderSection(
      { ...defaultSettings, previewHidden: ['questions.json'] },
      makeWorkflow([{ key: 'parse', label: '解析', outputs: ['questions.json'] }])
    )

    const checkbox = screen.getByRole('checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(false)

    fireEvent.click(checkbox)
    expect(setSettings).toHaveBeenCalledWith({ previewHidden: [] })
  })

  it('取消勾选加入隐藏列表（去重排序）', () => {
    const { setSettings } = renderSection(
      { ...defaultSettings, previewHidden: ['b.json'] },
      makeWorkflow([{ key: 'parse', label: '解析', outputs: ['a.json', 'b.json'] }])
    )

    const checkboxes = screen.getAllByRole('checkbox')
    // a.json 勾选中 → 点击取消。
    fireEvent.click(checkboxes[0])
    expect(setSettings).toHaveBeenCalledWith({
      previewHidden: ['a.json', 'b.json'],
    })
  })

  it('workflow 未加载时提示', () => {
    renderSection(defaultSettings, null)

    expect(screen.getByText('当前工作流未声明产物文件。')).toBeInTheDocument()
  })
})
