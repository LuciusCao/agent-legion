/**
 * previewDisplayMode 持久化测试（issue #528，jsdom 环境经
 * browserTestFiles 注册——源文件触碰 window.localStorage）：默认 custom、
 * 读写往返、损坏/未知值回退默认、按 workspace 隔离。
 * 组件级行为（开关渲染/切换/draftPreview 优先）见
 * PreviewPanelSection.mode.test.tsx。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  loadPreviewDisplayMode,
  savePreviewDisplayMode,
} from './previewDisplayMode'

function installLocalStorageStub() {
  const store = new Map<string, string>()
  const stub: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  }
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: stub,
  })
  return stub
}

const localStorageStub = installLocalStorageStub()

beforeEach(() => {
  localStorageStub.clear()
})

describe('previewDisplayMode', () => {
  it('无存储时默认 custom（现状：定制面板优先）', () => {
    expect(loadPreviewDisplayMode('ws1')).toBe('custom')
  })

  it('读写往返，按 workspace 隔离', () => {
    savePreviewDisplayMode('ws1', 'original')
    expect(loadPreviewDisplayMode('ws1')).toBe('original')
    expect(loadPreviewDisplayMode('ws2')).toBe('custom')
    savePreviewDisplayMode('ws1', 'custom')
    expect(loadPreviewDisplayMode('ws1')).toBe('custom')
  })

  it('损坏/未知值回退默认 custom', () => {
    window.localStorage.setItem('preview-panel-display-mode:ws1', 'weird')
    expect(loadPreviewDisplayMode('ws1')).toBe('custom')
  })
})
