import { useRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StudioChatWindow } from './StudioChatWindow'
import { StudioChatToolCallCard } from './StudioChatToolCallCard'

function Conversation({ count }: { count: number }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(false)
  return (
    <div ref={scrollRef} data-testid="scroll">
      <StudioChatWindow
        ids={Array.from({ length: count }, (_, index) => String(index))}
        scrollRef={scrollRef}
        pinnedRef={pinnedRef}
        renderRow={(index) => (
          <StudioChatToolCallCard
            call={{
              toolCallId: String(index),
              title: `Tool ${index}`,
              status: 'completed',
              rawInput: { payload: `input ${index}` },
              outputText: `output ${index}`,
            }}
          />
        )}
      />
    </div>
  )
}

describe('StudioChatWindow', () => {
  beforeEach(() => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 600,
      height: 100,
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 600,
      bottom: 100,
      toJSON: () => ({}),
    })
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(100)
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(600)
  })
  afterEach(() => vi.restoreAllMocks())

  it('bounds mounted tool cards and releases expanded bodies when scrolling away', () => {
    render(<Conversation count={1000} />)
    expect(screen.getAllByRole('button').length).toBeLessThan(30)
    fireEvent.click(screen.getByRole('button', { name: /Tool 0\b/ }))
    expect(screen.getByText(/input 0/)).toBeInTheDocument()
    fireEvent.scroll(screen.getByTestId('scroll'), {
      target: { scrollTop: 55000 },
    })
    expect(screen.queryByText(/input 0/)).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /Tool 0\b/ })
    ).not.toBeInTheDocument()
    expect(screen.getAllByRole('button').length).toBeLessThan(30)
    fireEvent.scroll(screen.getByTestId('scroll'), { target: { scrollTop: 0 } })
    expect(screen.getByRole('button', { name: /Tool 0\b/ })).toHaveAttribute(
      'aria-expanded',
      'false'
    )
  })

  it('keeps short conversations fully visible', () => {
    render(<Conversation count={10} />)
    expect(screen.getAllByRole('button')).toHaveLength(10)
  })
})
