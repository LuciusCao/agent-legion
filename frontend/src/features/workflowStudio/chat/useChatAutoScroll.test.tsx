import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatAutoScroll } from './useChatAutoScroll'
import { useChatPrependAnchor } from './useChatPrependAnchor'
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
  metrics: { scrollHeight: number; clientHeight: number }
) {
  Object.defineProperty(el, 'scrollHeight', {
    configurable: true,
    get: () => metrics.scrollHeight,
  })
  Object.defineProperty(el, 'clientHeight', {
    configurable: true,
    get: () => metrics.clientHeight,
  })
}

function stubRect(el: Element, rect: { top: number; bottom: number }) {
  Object.defineProperty(el, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({
      width: 600,
      height: rect.bottom - rect.top,
      x: 0,
      y: rect.top,
      top: rect.top,
      left: 0,
      right: 600,
      bottom: rect.bottom,
      toJSON: () => ({}),
    }),
  })
}

/** ResizeObserver 桩：捕获实例与观察目标集合，供测试手动触发回调
 * （jsdom 无 RO）。 */
class ResizeObserverStub {
  static instances: ResizeObserverStub[] = []
  readonly observed = new Set<Element>()
  private readonly callback: ResizeObserverCallback
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    ResizeObserverStub.instances.push(this)
  }
  observe(el: Element) {
    this.observed.add(el)
  }
  unobserve(el: Element) {
    this.observed.delete(el)
  }
  disconnect() {
    this.observed.clear()
  }
  fire() {
    this.callback([], this as unknown as ResizeObserver)
  }
}

// 与生产组合一致（StudioChatMessageList）：useChatPrependAnchor 产出到顶
// 回调（锚点捕获 + 触发加载），交给 useChatAutoScroll 的滚动处理器。
function Harness({
  messages,
  virtual = false,
  onLoadOlder,
}: {
  messages: ChatMessage[]
  virtual?: boolean
  onLoadOlder?: () => void
}) {
  const onNearTop = useChatPrependAnchor(messages, onLoadOlder ?? (() => {}))
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

describe('useChatAutoScroll + useChatPrependAnchor（#1120 PR-4 / #1237）', () => {
  beforeEach(() => {
    ResizeObserverStub.instances = []
    vi.stubGlobal('ResizeObserver', ResizeObserverStub)
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
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('滚动到顶（<80px）触发加载回调；远离顶部不触发', () => {
    const onLoadOlder = vi.fn()
    render(<Harness messages={batch('m', 1, 10)} onLoadOlder={onLoadOlder} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 1000, clientHeight: 400 })

    el.scrollTop = 200
    fireEvent.scroll(el)
    expect(onLoadOlder).not.toHaveBeenCalled()

    el.scrollTop = 50
    fireEvent.scroll(el)
    expect(onLoadOlder).toHaveBeenCalledTimes(1)
  })

  it('prepend 按旧首行元素的实测位移复原视口；触发→响应窗口内的测量修正不混入（#1237 codex P2-1）', () => {
    const initial = batch('m', 1, 10)
    const view = render(<Harness messages={initial} onLoadOlder={() => {}} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 1000, clientHeight: 400 })
    el.scrollTop = 50
    // 触发分页：捕获首行元素锚点（全局 mock 下各行 rect top=0）。
    fireEvent.scroll(el)
    const anchorRow = el.children[0]

    // 触发→响应窗口内的内容高度变化（虚拟器测量修正/卡片展开）：
    // scrollHeight 1000→1300——不改变 messages、不触发滚动事件。
    stubMetrics(el, { scrollHeight: 1300, clientHeight: 400 })

    // prepend 一页：锚定行被下推 600（keyed 重挂载保持同一 DOM 节点）。
    stubRect(anchorRow, { top: 600, bottom: 700 })
    stubMetrics(el, { scrollHeight: 1900, clientHeight: 400 })
    view.rerender(
      <Harness
        messages={[...batch('o', -19, 0), ...initial]}
        onLoadOlder={() => {}}
      />
    )

    // 视口不跳：补偿 = 锚定行实测位移 600；若按 scrollHeight 差分会把
    // 300 修正量混入（900，视口过冲）。
    expect(el.scrollTop).toBe(650)
  })

  it('尾部追加不补偿（scrollTop 保持原位）', () => {
    const initial = batch('m', 1, 10)
    const view = render(<Harness messages={initial} onLoadOlder={() => {}} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 1000, clientHeight: 400 })
    el.scrollTop = 50
    fireEvent.scroll(el)
    view.rerender(<Harness messages={[...initial]} onLoadOlder={() => {}} />)

    stubMetrics(el, { scrollHeight: 1200, clientHeight: 400 })
    view.rerender(
      <Harness
        messages={[...initial, ...batch('n', 11, 12)]}
        onLoadOlder={() => {}}
      />
    )
    expect(el.scrollTop).toBe(50)
  })

  it('整体替换（旧首条已不在列表，如切换会话）不补偿', () => {
    const initial = batch('m', 1, 10)
    const view = render(<Harness messages={initial} onLoadOlder={() => {}} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 1000, clientHeight: 400 })
    el.scrollTop = 50
    fireEvent.scroll(el)
    view.rerender(<Harness messages={[...initial]} onLoadOlder={() => {}} />)

    stubMetrics(el, { scrollHeight: 3000, clientHeight: 400 })
    view.rerender(
      <Harness messages={batch('x', 100, 109)} onLoadOlder={() => {}} />
    )
    expect(el.scrollTop).toBe(50)
  })

  it('虚拟态（>80 条）锚定行被裁出挂载窗口时回退高度差分，基线由 ResizeObserver 持续同步（#1237 codex P2-1/P2-2）', () => {
    // 100 条已在虚拟态 → prepend 100 条后 200 条；scrollTop=50 的挂载窗口
    // 只含新页行，旧首行（m101）被裁出 → 元素锚定不可用，回退差分。
    const initial = batch('m', 101, 200)
    const view = render(
      <Harness messages={initial} virtual onLoadOlder={() => {}} />
    )
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 10990, clientHeight: 400 })
    el.scrollTop = 50
    // 触发分页：差分基线 10990 + 捕获首行锚点。
    fireEvent.scroll(el)
    expect(ResizeObserverStub.instances.length).toBeGreaterThan(0)

    // 触发→响应窗口内的测量修正：10990→11590；RO 回调把基线同步到
    // 11590——修正量不混入随后的 prepend 补偿。
    stubMetrics(el, { scrollHeight: 11590, clientHeight: 400 })
    act(() => {
      for (const observer of ResizeObserverStub.instances) observer.fire()
    })

    // prepend 100 行（虚拟器总高 +11000）。
    stubMetrics(el, { scrollHeight: 22590, clientHeight: 400 })
    view.rerender(
      <Harness
        messages={[...batch('o', 1, 100), ...initial]}
        virtual
        onLoadOlder={() => {}}
      />
    )
    expect(el.scrollTop).toBe(50 + 11000)
    // 确实处于虚拟态：未挂载全部行。
    expect(el.querySelectorAll('[data-index]').length).toBeLessThan(200)
  })

  it('非虚拟→虚拟态交界后重挂观察：spacer 新节点纳入基线同步（#1237 复验 P3-b）', () => {
    // 70 条非虚拟 → prepend 100 条后 170 条进入虚拟态：spacer 是新 DOM
    // 节点。只观察首次触发时 children 的实现会让基线同步静默失效。
    const initial = batch('m', 71, 140)
    const view = render(<Harness messages={initial} onLoadOlder={() => {}} />)
    const el = screen.getByTestId('list')
    stubMetrics(el, { scrollHeight: 7690, clientHeight: 400 })
    el.scrollTop = 50
    fireEvent.scroll(el)
    const observer = ResizeObserverStub.instances[0]
    expect(observer.observed.size).toBeGreaterThan(0)

    // prepend 100 行 → 越过 >80 阈值进入虚拟态（spacer 挂载）。
    stubMetrics(el, { scrollHeight: 18690, clientHeight: 400 })
    view.rerender(
      <Harness
        messages={[...batch('o', 1, 100), ...initial]}
        virtual
        onLoadOlder={() => {}}
      />
    )

    // 再次到顶触发：观察目标必须重挂到当前 children（含新 spacer）。
    el.scrollTop = 50
    fireEvent.scroll(el)
    const spacer = el.firstElementChild!
    expect(spacer.querySelector(':scope > [data-index]')).not.toBeNull()
    expect(observer.observed.has(spacer)).toBe(true)
    // 观察集合与当前 children 一致（旧行节点不再滞留）。
    expect(observer.observed.size).toBe(el.children.length)
    for (const child of el.children)
      expect(observer.observed.has(child)).toBe(true)
  })
})
