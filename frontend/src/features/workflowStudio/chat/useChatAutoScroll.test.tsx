import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatAutoScroll } from './useChatAutoScroll'
import { StudioChatWindow } from './StudioChatWindow'
import type { ChatMessage } from './studioChatMessages'

function textMessage(id: string, seq: number): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind: 'text',
    role: 'agent',
    content: { text: id },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function batch(prefix: string, from: number, to: number): ChatMessage[] {
  return Array.from({ length: to - from + 1 }, (_, i) =>
    textMessage(`${prefix}${from + i}`, from + i)
  )
}

/** jsdom 无布局：scrollHeight/clientHeight 用 per-element getter 注入
 * （scrollTop 在 jsdom 原生可读写在实例上）。 */
function stubMetrics(
  el: HTMLElement,
  metrics: { scrollHeight: number | (() => number); clientHeight: number }
) {
  Object.defineProperty(el, 'scrollHeight', {
    configurable: true,
    get:
      typeof metrics.scrollHeight === 'function'
        ? metrics.scrollHeight
        : () => metrics.scrollHeight,
  })
  Object.defineProperty(el, 'clientHeight', {
    configurable: true,
    get: () => metrics.clientHeight,
  })
}

function Harness({
  messages,
  virtual = false,
  onNearTop,
}: {
  messages: ChatMessage[]
  virtual?: boolean
  onNearTop?: () => void
}) {
  const { bottomRef, listRef, handleScroll, pinnedToBottomRef } =
    useChatAutoScroll(messages, onNearTop)
  return (
    <div ref={listRef} data-testid="list" onScroll={handleScroll}>
      {virtual ? (
        <StudioChatWindow
          ids={messages.map((message) => message.id)}
          scrollRef={listRef}
          pinnedRef={pinnedToBottomRef}
          renderRow={(index) => <div>{messages[index].id}</div>}
        />
      ) : (
        messages.map((message) => <div key={message.id}>{message.id}</div>)
      )}
      <div ref={bottomRef} />
    </div>
  )
}

describe('useChatAutoScroll 历史翻页触发与锚定补偿（#1120 PR-4）', () => {
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

  it('滚动到顶（<80px）触发 onNearTop；远离顶部不触发', () => {
    const onNearTop = vi.fn()
    render(<Harness messages={batch('m', 1, 10)} onNearTop={onNearTop} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 1000, clientHeight: 400 })

    el.scrollTop = 200
    fireEvent.scroll(el)
    expect(onNearTop).not.toHaveBeenCalled()

    el.scrollTop = 50
    fireEvent.scroll(el)
    expect(onNearTop).toHaveBeenCalledTimes(1)
  })

  it('prepend 后按 scrollHeight 增量补偿 scrollTop（非虚拟态）', () => {
    const initial = batch('m', 1, 10)
    const view = render(<Harness messages={initial} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 1000, clientHeight: 400 })
    el.scrollTop = 50
    fireEvent.scroll(el)
    // 同内容新数组触发一次 layout effect：快照 prepend 前的 scrollHeight。
    view.rerender(<Harness messages={[...initial]} />)

    // prepend 一页旧消息：容器内容增高 600（模拟渲染增长）。
    stubMetrics(el, { scrollHeight: 1600, clientHeight: 400 })
    view.rerender(<Harness messages={[...batch('o', -19, 0), ...initial]} />)

    // 视口不跳：scrollTop 补偿 = 50 + (1600 - 1000)。
    expect(el.scrollTop).toBe(650)
  })

  it('尾部追加不补偿（scrollTop 保持原位）', () => {
    const initial = batch('m', 1, 10)
    const view = render(<Harness messages={initial} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 1000, clientHeight: 400 })
    el.scrollTop = 50
    fireEvent.scroll(el)
    view.rerender(<Harness messages={[...initial]} />)

    stubMetrics(el, { scrollHeight: 1200, clientHeight: 400 })
    view.rerender(<Harness messages={[...initial, ...batch('n', 11, 12)]} />)
    expect(el.scrollTop).toBe(50)
  })

  it('整体替换（旧首条已不在列表，如切换会话）不补偿', () => {
    const initial = batch('m', 1, 10)
    const view = render(<Harness messages={initial} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 1000, clientHeight: 400 })
    el.scrollTop = 50
    fireEvent.scroll(el)
    view.rerender(<Harness messages={[...initial]} />)

    stubMetrics(el, { scrollHeight: 3000, clientHeight: 400 })
    view.rerender(<Harness messages={batch('x', 100, 109)} />)
    expect(el.scrollTop).toBe(50)
  })

  it('虚拟滚动态（>80 条）prepend 同样锚定', () => {
    // 100 条已在虚拟态 → prepend 100 条后 200 条。scrollHeight 固定注入
    // （jsdom mock 下虚拟器的测量修正会在 commit 后漂移，不可作确定断言；
    // 补偿机制按容器 scrollHeight 增量工作，两态同一代码路径——本用例的
    // 增量断言与「确实处于虚拟态」的窗口化断言）。
    const initial = batch('m', 101, 200)
    const view = render(<Harness messages={initial} virtual />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 10990, clientHeight: 400 })
    el.scrollTop = 200
    fireEvent.scroll(el)
    view.rerender(<Harness messages={[...initial]} virtual />)

    stubMetrics(el, { scrollHeight: 21990, clientHeight: 400 })
    view.rerender(
      <Harness messages={[...batch('o', 1, 100), ...initial]} virtual />
    )
    expect(el.scrollTop).toBe(200 + 11000)
    // 确实处于虚拟态：未挂载全部行。
    expect(el.querySelectorAll('[data-index]').length).toBeLessThan(200)
  })

  it('补偿基线取触发分页时的当前高度（虚拟器测量修正不混入增量，#1237 codex P2-2）', () => {
    // 虚拟列表的 measureElement/ResizeObserver 修正不改变 messages，只在
    // 消息变化时快照的基线会滞留初始估算值，把累计修正混进 prepend 增量。
    const initial = batch('m', 1, 10)
    const onNearTop = () => {}
    const view = render(<Harness messages={initial} onNearTop={onNearTop} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 1000, clientHeight: 400 })
    el.scrollTop = 50
    fireEvent.scroll(el)
    view.rerender(<Harness messages={[...initial]} onNearTop={onNearTop} />)

    // 测量修正把内容高度从 1000 改到 1300（messages 未变、无 effect 重跑），
    // 用户继续滚动并再次到顶触发分页——基线必须取此刻的 1300。
    stubMetrics(el, { scrollHeight: 1300, clientHeight: 400 })
    el.scrollTop = 40
    fireEvent.scroll(el)

    // prepend 一页：内容再增高 600。补偿 = 40 + 600；滞留基线会算出
    // 40 + 900（混入 300 修正量，视口过冲）。
    stubMetrics(el, { scrollHeight: 1900, clientHeight: 400 })
    view.rerender(
      <Harness
        messages={[...batch('o', -19, 0), ...initial]}
        onNearTop={onNearTop}
      />
    )
    expect(el.scrollTop).toBe(640)
  })
})
