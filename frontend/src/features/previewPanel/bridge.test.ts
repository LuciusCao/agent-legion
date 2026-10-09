/** 桥协议消息守卫的纯单测（issue #328，#1146 增补）：协议字段变更在这里炸出来。 */
import { describe, it, expect } from 'vitest'
import {
  isHostToPanelMessage,
  isPanelToHostMessage,
  PREVIEW_HOST_CAPABILITIES,
  PREVIEW_HOST_SOURCE,
  PREVIEW_PANEL_SOURCE,
} from './bridge'

describe('isPanelToHostMessage', () => {
  it('接受 ready / resize / 四种只读 request', () => {
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
    for (const method of [
      'listArtifacts',
      'readArtifact',
      'readArtifactBytes',
      'getJobDetail',
    ]) {
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
    // #989 宿主探针消息：directive 必须是字符串。
    expect(
      isPanelToHostMessage({
        source: PREVIEW_PANEL_SOURCE,
        type: 'csp-violation',
        directive: 'script-src-attr',
      })
    ).toBe(true)
    expect(
      isPanelToHostMessage({
        source: PREVIEW_PANEL_SOURCE,
        type: 'csp-violation',
      })
    ).toBe(false)
    expect(isPanelToHostMessage({ source: 'other', type: 'ready' })).toBe(false)
    expect(isPanelToHostMessage(null)).toBe(false)
    expect(isPanelToHostMessage('ready')).toBe(false)
  })
})

describe('isHostToPanelMessage', () => {
  it('接受 init（含/不含 capabilities）与 response', () => {
    expect(
      isHostToPanelMessage({
        source: PREVIEW_HOST_SOURCE,
        type: 'init',
        jobId: 'j1',
        theme: {},
        assets: {},
        // #1146：能力声明随 init 下发，面板据此对 readArtifactBytes 分支。
        capabilities: ['readArtifactBytes'],
      })
    ).toBe(true)
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

  it('宿主能力声明当前只含 readArtifactBytes（基础三法必有，不列条目）', () => {
    expect(PREVIEW_HOST_CAPABILITIES).toEqual(['readArtifactBytes'])
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
