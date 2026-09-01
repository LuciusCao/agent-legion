/** 桥协议消息守卫的纯单测（issue #328）：协议字段变更在这里炸出来。 */
import { describe, it, expect } from 'vitest'
import {
  isHostToPanelMessage,
  isPanelToHostMessage,
  PREVIEW_HOST_SOURCE,
  PREVIEW_PANEL_SOURCE,
} from './bridge'

describe('isPanelToHostMessage', () => {
  it('接受 ready / resize / 三种只读 request', () => {
    expect(
      isPanelToHostMessage({ source: PREVIEW_PANEL_SOURCE, type: 'ready' })
    ).toBe(true)
    expect(
      isPanelToHostMessage({
        source: PREVIEW_PANEL_SOURCE,
        type: 'resize',
        height: 320,
      })
    ).toBe(true)
    for (const method of ['listArtifacts', 'readArtifact', 'getJobDetail']) {
      expect(
        isPanelToHostMessage({
          source: PREVIEW_PANEL_SOURCE,
          type: 'request',
          id: 1,
          method,
        })
      ).toBe(true)
    }
  })

  it('拒绝未知方法 / 缺字段 / 错误来源标记', () => {
    // 桥方法表是只读契约：未列入的方法在守卫处就被丢弃，不会到达宿主处理。
    expect(
      isPanelToHostMessage({
        source: PREVIEW_PANEL_SOURCE,
        type: 'request',
        id: 1,
        method: 'deleteJob',
      })
    ).toBe(false)
    expect(
      isPanelToHostMessage({
        source: PREVIEW_PANEL_SOURCE,
        type: 'resize',
        height: '320',
      })
    ).toBe(false)
    expect(isPanelToHostMessage({ source: 'other', type: 'ready' })).toBe(false)
    expect(isPanelToHostMessage(null)).toBe(false)
    expect(isPanelToHostMessage('ready')).toBe(false)
  })
})

describe('isHostToPanelMessage', () => {
  it('接受 init / response', () => {
    expect(
      isHostToPanelMessage({
        source: PREVIEW_HOST_SOURCE,
        type: 'init',
        jobId: 'j1',
        theme: {},
        assets: {},
      })
    ).toBe(true)
    expect(
      isHostToPanelMessage({
        source: PREVIEW_HOST_SOURCE,
        type: 'response',
        id: 1,
        ok: true,
      })
    ).toBe(true)
  })

  it('拒绝缺字段与错误来源', () => {
    expect(
      isHostToPanelMessage({ source: PREVIEW_HOST_SOURCE, type: 'init' })
    ).toBe(false)
    expect(
      isHostToPanelMessage({
        source: PREVIEW_PANEL_SOURCE,
        type: 'response',
        id: 1,
        ok: true,
      })
    ).toBe(false)
  })
})
