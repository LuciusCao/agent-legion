/** 桥协议消息守卫的纯单测（issue #328，#1146 增补）：协议字段变更在这里炸出来。 */
import { describe, it, expect } from 'vitest'
import {
  BYTE_PORT_OFFER_TYPE,
  isHostToPanelMessage,
  isPanelToHostMessage,
  PREVIEW_HOST_CAPABILITIES,
  PREVIEW_HOST_SOURCE,
  PREVIEW_PANEL_SOURCE,
} from './bridge'
import { isPortRequestMessage } from './bytePortServer'

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

  it('接受 byte-port-offer（#1178 P1：bootstrap 上交字节桥端口；端口在 event.ports，data 仅类型标记）', () => {
    expect(
      isPanelToHostMessage({
        source: PREVIEW_PANEL_SOURCE,
        type: 'byte-port-offer',
      })
    ).toBe(true)
    expect(
      isPanelToHostMessage({ source: 'other', type: 'byte-port-offer' })
    ).toBe(false)
  })

  it('BYTE_PORT_OFFER_TYPE 常量与守卫判定同源（防字面量漂移）', () => {
    expect(BYTE_PORT_OFFER_TYPE).toBe('byte-port-offer')
    expect(
      isPanelToHostMessage({
        source: PREVIEW_PANEL_SOURCE,
        type: BYTE_PORT_OFFER_TYPE,
      })
    ).toBe(true)
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

describe('isPortRequestMessage（#1178 codex 复审 P1：port 通道的 request 判定）', () => {
  it('port request 无 source 标记——身份由端口持有本身证明', () => {
    expect(
      isPortRequestMessage({
        type: 'request',
        id: 1,
        method: 'readArtifactBytes',
      })
    ).toBe(true)
    expect(
      isPortRequestMessage({
        type: 'request',
        id: 2,
        method: 'getJobDetail',
        params: {},
      })
    ).toBe(true)
  })

  it('拒绝缺字段；未知方法由 Host 层 methodGuard 拒（形态判定通过）', () => {
    expect(
      isPortRequestMessage({ type: 'request', method: 'listArtifacts' })
    ).toBe(false)
    expect(
      isPortRequestMessage({
        type: 'request',
        id: 1,
        method: 'destroyEverything',
      })
    ).toBe(true)
    // port 通道判定不看 source（身份由端口本身证明）——window 形态带
    // source 不会因此被拒，但宿主从未把 port 交给非初始文档，判定本身
    // 无需 source 语义。
    expect(
      isPortRequestMessage({
        source: 'agent-legion-preview-panel',
        type: 'request',
        id: 1,
        method: 'readArtifact',
      })
    ).toBe(true)
  })
})
