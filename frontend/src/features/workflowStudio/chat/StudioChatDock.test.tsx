/**
 * StudioChatDock 的窄屏节点定位组合（#812 对抗轮 D6，jsdom）：窄屏 Agent
 * 页签内从 Dock 点节点定位时，全宽节点抽屉挂在 SplitLayout 层——不切回
 * 画布页签会盖住 Dock 与页签导航；onSelectNode 必须先切页签再发定位请求。
 */
import { act, render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { StudioChatDock } from './StudioChatDock'
import {
  makeStudioView,
  withStudioProviders,
} from '../shared/testStudioProviders'
import { useSettingStore } from '../../../stores/settingStore'

// AgentPanelDock 本体（Portal/Rnd/几何记忆）与 D6 无关：打桩成透传容器，
// 让子树（StudioChatPanel mock）挂载。
vi.mock('../../agentPanelDock/AgentPanelDock', () => ({
  AgentPanelDock: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}))

const chatPanelProps = vi.fn()
vi.mock('./StudioChatPanel', () => ({
  StudioChatPanel: (props: Record<string, unknown>) => {
    chatPanelProps(props)
    return <div>chat panel stub</div>
  },
}))

vi.mock('../shared/useAgentPublishRequest', () => ({
  useAgentPublishRequest: () => ({
    resolvedNotice: null,
    clearNotice: vi.fn(),
  }),
}))
vi.mock('../shared/useStudioMobileNavHeight', () => ({
  useStudioMobileNavHeight: () => 0,
}))

// 窄屏判定桩：可翻转（matchMedia stub 恒 false 走不了真断点）。
const narrowState = { value: false }
vi.mock('../shared/useStudioNarrowViewport', () => ({
  useStudioNarrowViewport: () => narrowState.value,
}))

function renderDock(viewOverrides: Record<string, unknown> = {}) {
  const studio = {
    selectedNodeKey: null,
    requestNodeFocus: vi.fn(),
  }
  const view = makeStudioView(viewOverrides)
  render(withStudioProviders(studio, view, <StudioChatDock hidden={false} />))
  return { studio, view }
}

function lastOnSelectNode(): (nodeKey: string) => void {
  const props = chatPanelProps.mock.calls[
    chatPanelProps.mock.calls.length - 1
  ]?.[0] as { onSelectNode: (nodeKey: string) => void }
  return props.onSelectNode
}

describe('StudioChatDock（#812 D6：窄屏节点定位先切画布页签）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    narrowState.value = false
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('窄屏 Agent 页签内定位节点：先切回画布页签，再发定位请求', () => {
    narrowState.value = true
    const setMobilePanel = vi.fn()
    const { studio } = renderDock({
      mobilePanel: 'agent',
      setMobilePanel,
    })

    act(() => lastOnSelectNode()('node-a'))

    // revert 即红：直接转发 requestNodeFocus 时页签滞留 agent，全宽抽屉
    // 盖住 Dock 与页签导航。
    expect(setMobilePanel).toHaveBeenCalledWith('graph')
    expect(studio.requestNodeFocus).toHaveBeenCalledWith('node-a')
  })

  it('窄屏但已在画布页签：不重复切页签', () => {
    narrowState.value = true
    const setMobilePanel = vi.fn()
    const { studio } = renderDock({
      mobilePanel: 'graph',
      setMobilePanel,
    })

    act(() => lastOnSelectNode()('node-a'))

    expect(setMobilePanel).not.toHaveBeenCalled()
    expect(studio.requestNodeFocus).toHaveBeenCalledWith('node-a')
  })

  it('宽屏定位节点不动页签（宽屏抽屉是右侧浮层，不盖 Dock）', () => {
    const setMobilePanel = vi.fn()
    const { studio } = renderDock({
      mobilePanel: 'agent',
      setMobilePanel,
    })

    act(() => lastOnSelectNode()('node-a'))

    expect(setMobilePanel).not.toHaveBeenCalled()
    expect(studio.requestNodeFocus).toHaveBeenCalledWith('node-a')
  })
})
