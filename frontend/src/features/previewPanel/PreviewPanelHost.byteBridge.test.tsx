/**
 * PreviewPanelHost 字节桥契约测试（#1146 增补媒体字节通道，#1178 多轮收口；
 * 从 PreviewPanelHost.test.tsx 按被测主题拆出——codex 复审 P1：原文件超
 * 800 行主动拆分阈值；共享夹具在 previewHostTestlib.tsx，用例零改动迁移）：
 * - 字节桥端口由面板初始文档的注入 bootstrap 上交：每个挂载只接受第一次
 *   上交，伪造/重复上交被拒；同挂载第二次 load（= 面板自导航）整桥撤销；
 * - port 通道只服务 readArtifactBytes（payload 逐字节相等 / 超限走错误
 *   响应 / transfer 零拷贝）；窗口通道恒拒并引导到注入全局；
 * - init 只带数据、永不携带端口（重发也不重新发放能力）；
 * - 内存护栏：字节读取每 port 串行化；端口关闭时中止在途读取、丢弃排队
 *   项并抑制滞留响应。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, waitFor, fireEvent } from '@testing-library/react'
import { PREVIEW_PANEL_SOURCE } from './bridge'
import {
  bridgeReady,
  emitPanelMessage,
  flush,
  getIframe,
  hostReplies,
  mockFetchJobArtifactRawBytes,
  offerBytePort,
  portInbox,
  renderHost,
  resetPreviewHostMocks,
} from './previewHostTestlib'

vi.mock('../../api', () => ({
  fetchJobArtifactRawBytes: (...args: unknown[]) =>
    mockFetchJobArtifactRawBytes(...args),
}))

beforeEach(() => {
  resetPreviewHostMocks()
})

describe('PreviewPanelHost 字节桥（#1146/#1178）', () => {
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
      'demo.mp4',
      { signal: expect.any(AbortSignal) }
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

  it('关闭端口时中止在途读取、丢弃排队项并抑制滞留响应（#1178 codex 复审 P2）', async () => {
    // 帧下架/自导航后若继续跑队列，每个最多 512 MiB 的响应会无处投递仍
    // 耗尽带宽与内存——close 路径必须中止在途 fetch 并丢弃未开始的项。
    let firstSignal: AbortSignal | undefined
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
      .mockImplementationOnce(
        (_jobId: string, _name: string, options?: { signal?: AbortSignal }) => {
          firstSignal = options?.signal
          return firstGate
        }
      )
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
      id: 81,
      method: 'readArtifactBytes',
      params: { name: 'a.mp4' },
    })
    panelPort.postMessage({
      type: 'request',
      id: 82,
      method: 'readArtifactBytes',
      params: { name: 'b.mp4' },
    })
    await waitFor(() =>
      expect(mockFetchJobArtifactRawBytes).toHaveBeenCalledTimes(1)
    )

    // 面板自导航 → 第二次 load → acceptor.close()（先取消再关端口）。
    act(() => {
      fireEvent.load(iframe)
    })
    expect(firstSignal?.aborted).toBe(true)

    // 释放在途读取：响应被抑制（帧已下架）；排队项被丢弃、不再发起。
    releaseFirst({
      name: 'a.mp4',
      mediaType: 'video/mp4',
      bytes: new ArrayBuffer(1),
    })
    await flush()
    expect(mockFetchJobArtifactRawBytes).toHaveBeenCalledTimes(1)
    expect(inbox.filter((d) => d.type === 'response')).toEqual([])
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
})
