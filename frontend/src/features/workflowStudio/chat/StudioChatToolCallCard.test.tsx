import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StudioChatToolCallCard } from './StudioChatToolCallCard'
import type { ToolCallView } from './studioChatMessages'
import { OUTPUT_PREVIEW_CHAR_LIMIT } from './studioChatTruncation'

function call(partial: Partial<ToolCallView>): ToolCallView {
  return {
    toolCallId: 't1',
    title: 'some_tool',
    status: 'completed',
    rawInput: null,
    outputText: '',
    ...partial,
  }
}

function renderOpenCard(view: ToolCallView) {
  const rendered = render(<StudioChatToolCallCard call={view} />)
  fireEvent.click(
    rendered.getByRole('button', { name: new RegExp(view.title) })
  )
  return rendered
}

describe('StudioChatToolCallCard 输出截断（#1120）', () => {
  it('renders short output in full with no expand outlet', () => {
    const rendered = renderOpenCard(call({ outputText: '短短一行' }))
    expect(rendered.container.querySelector('pre')).toHaveTextContent(
      '短短一行'
    )
    expect(rendered.queryByRole('button', { name: /查看完整/ })).toBeNull()
  })

  it('truncates long output to the preview limit and expands on demand', () => {
    const tail = 'TAIL_MARKER'
    const long = 'x'.repeat(OUTPUT_PREVIEW_CHAR_LIMIT + 100) + tail
    const rendered = renderOpenCard(call({ outputText: long }))
    const pre = rendered.container.querySelector('pre')!
    expect(pre.textContent).toBe('x'.repeat(OUTPUT_PREVIEW_CHAR_LIMIT))
    expect(pre.textContent).not.toContain(tail)
    const expand = rendered.getByRole('button', { name: /查看完整输出/ })
    expect(expand).toHaveTextContent(String(long.length))
    fireEvent.click(expand)
    // 出口点开后才渲染全量：pre 内容与原始 outputText 完全相等，
    // 证明截断只做在渲染层、数据未被改写。
    expect(rendered.container.querySelector('pre')!.textContent).toBe(long)
    expect(rendered.queryByRole('button', { name: /查看完整输出/ })).toBeNull()
  })

  it('truncates long rawInput JSON with its own expand outlet', () => {
    const rawInput = { payload: 'y'.repeat(OUTPUT_PREVIEW_CHAR_LIMIT + 50) }
    const full = JSON.stringify(rawInput, null, 2)
    const rendered = renderOpenCard(call({ rawInput }))
    const expand = rendered.getByRole('button', { name: /查看完整输入/ })
    expect(rendered.container.querySelector('pre')!.textContent).toBe(
      full.slice(0, OUTPUT_PREVIEW_CHAR_LIMIT)
    )
    fireEvent.click(expand)
    expect(rendered.container.querySelector('pre')!.textContent).toBe(full)
  })
})
