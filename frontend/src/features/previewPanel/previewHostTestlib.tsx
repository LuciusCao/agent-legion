/**
 * PreviewPanelHost 契约测试的共享夹具（#1178 codex 复审 P1：原测试文件超
 * 800 行主动拆分阈值，按被测主题拆为 PreviewPanelHost.test.tsx 与
 * PreviewPanelHost.byteBridge.test.tsx 两个姊妹文件，共享件沉到本模块；
 * 用例本身零改动迁移）。
 *
 * 注意 vi.mock 不能集中在这里（它按测试文件独立生效）——两个测试文件
 * 各自保留 vi.mock('../../api') 块，工厂闭包引用本模块导出的 mock 函数；
 * beforeEach 统一调 resetPreviewHostMocks()。
 */
import { vi } from 'vitest'
import { render, act } from '@testing-library/react'
import type { ReactElement } from 'react'
import { PreviewPanelHost } from './PreviewPanelHost'
import { PREVIEW_HOST_SOURCE, PREVIEW_PANEL_SOURCE } from './bridge'
import { makeJobDetail } from '../../testing/jobDetailFixtures'
import { TestQueryProvider } from '../../testing/testQueryClient'

export const mockFetchJobArtifact = vi.fn()
export const mockFetchJobDetail = vi.fn()
export const mockFetchJobArtifactRawBytes = vi.fn()

/** 每个用例的基线：清空调用史并给出默认 job detail（两个产物名）。 */
export function resetPreviewHostMocks() {
  mockFetchJobArtifact.mockReset()
  mockFetchJobDetail.mockReset()
  mockFetchJobArtifactRawBytes.mockReset()
  mockFetchJobDetail.mockResolvedValue(
    makeJobDetail([], { artifacts: ['questions.json', 'notes.md'] })
  )
}

export const BUNDLE = '<!doctype html><html><body>panel</body></html>'

export function renderHost(ui?: ReactElement) {
  return render(ui ?? <PreviewPanelHost jobId="job-1" html={BUNDLE} />, {
    wrapper: TestQueryProvider,
  })
}

export function getIframe(container: HTMLElement): HTMLIFrameElement {
  const iframe = container.querySelector('iframe')
  if (!iframe) throw new Error('iframe not rendered')
  return iframe
}

/** 冲刷异步更新（react-query 解析 + jsdom 的 iframe load 事件）进 act。 */
export async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
}

/**
 * 等待桥端点就绪：jsdom 对 srcdoc iframe 异步 fire load，宿主监听在 mount
 * effect 里登记——发桥消息前 flush 一次，保证两者就位（生产语义一致：
 * 面板脚本只在其文档解析后才运行，晚于宿主 listener 挂上）。
 */
export async function bridgeReady() {
  await flush()
}

/** 以面板身份向宿主派发消息（jsdom 的 MessageEvent 支持 source 字段）。 */
export function emitPanelMessage(iframe: HTMLIFrameElement, data: unknown) {
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
export function offerBytePort(iframe: HTMLIFrameElement) {
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
export function portInbox(port: MessagePort): Array<Record<string, unknown>> {
  const messages: Array<Record<string, unknown>> = []
  port.onmessage = (event: MessageEvent) => {
    messages.push(event.data as Record<string, unknown>)
  }
  return messages
}

export function hostReplies(
  iframe: HTMLIFrameElement
): Array<Record<string, unknown>> {
  const spy = vi.mocked(iframe.contentWindow!.postMessage)
  return spy.mock.calls
    .map((call) => call[0] as Record<string, unknown>)
    .filter((data) => data.source === PREVIEW_HOST_SOURCE)
}
