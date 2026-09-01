/**
 * PreviewPanelHost 契约测试（issue #328 的质量红线）：
 * - sandbox 属性恒为 "allow-scripts"，永不出现 allow-same-origin；
 * - 只认 event.source === iframe.contentWindow 且带面板 source 标记的消息
 *   （opaque origin 下 event.origin 恒为 "null"，不能用于鉴别）；
 * - 桥方法只读：listArtifacts / readArtifact / getJobDetail；
 * - ready → 下发 init（jobId + --pp-* 主题变量 + katex 资源 URL）；
 * - resize 高度钳制在 [120, 6000]。
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

vi.mock('../../api', () => ({
  fetchJobArtifact: (...args: unknown[]) => mockFetchJobArtifact(...args),
  fetchJobDetail: (...args: unknown[]) => mockFetchJobDetail(...args),
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
    expect(iframe.getAttribute('srcdoc')).toBe(BUNDLE)
    await flush()
  })
})

describe('PreviewPanelHost 桥协议', () => {
  it('ready 后向面板下发 init（jobId + 主题变量 + 资源）', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    const postSpy = vi.spyOn(iframe.contentWindow!, 'postMessage')

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
  })

  it('listArtifacts 返回 job detail 的产物清单', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    vi.spyOn(iframe.contentWindow!, 'postMessage')
    emitPanelMessage(iframe, { source: PREVIEW_PANEL_SOURCE, type: 'ready' })
    await waitFor(() => expect(mockFetchJobDetail).toHaveBeenCalled())

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

  it('readArtifact 缺 name 与未知方法回结构化错误', async () => {
    const { container } = renderHost()
    const iframe = getIframe(container)
    vi.spyOn(iframe.contentWindow!, 'postMessage')

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
