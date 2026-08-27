import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StudioChatInput } from './StudioChatInput'

function renderInput(
  overrides?: Partial<Parameters<typeof StudioChatInput>[0]>
) {
  const onSend = vi.fn()
  render(
    <StudioChatInput
      busy={false}
      disabled={false}
      disabledReason={null}
      onSend={onSend}
      {...overrides}
    />
  )
  return onSend
}

describe('StudioChatInput', () => {
  it('does not send on Enter while an IME composition is active', () => {
    const onSend = renderInput()
    const input = screen.getByLabelText('消息输入')
    fireEvent.change(input, { target: { value: '你好' } })
    // 中文输入法组合中的回车是确认候选，不能当成发送。
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(onSend).not.toHaveBeenCalled()
    // 组合结束后的回车正常发送。
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false })
    expect(onSend).toHaveBeenCalledWith('你好')
  })

  it('keeps the input enabled while busy and labels the button 排队', () => {
    renderInput({ busy: true })
    expect(screen.getByLabelText('消息输入')).toBeEnabled()
    expect(screen.getByRole('button', { name: '排队' })).toBeInTheDocument()
    expect(screen.getByText(/运行中发送将进入队列/)).toBeInTheDocument()
  })
})
