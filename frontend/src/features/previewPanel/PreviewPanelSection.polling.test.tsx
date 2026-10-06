/**
 * PreviewPanelSection 治理面状态的轮询档位（#965，姊妹文件——按被测主题
 * 拆分）：admin 常驻轮询，但只有定制对话开着（agent 在写草稿）时 3s，
 * 关着 30s；非 admin 不轮询。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent } from '@testing-library/react'
import { PreviewPanelSection } from './PreviewPanelSection'
import type { PreviewPanelState } from './previewPanelApi'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { useAuthStore } from '../../stores/authStore'

const mockFetchPublished = vi.fn()
const mockFetchState = vi.fn()

vi.mock('./previewPanelApi', () => ({
  fetchPublishedPreviewPanel: (...args: unknown[]) =>
    mockFetchPublished(...args),
  fetchPreviewPanelState: (...args: unknown[]) => mockFetchState(...args),
  publishPreviewPanel: vi.fn(),
  archivePreviewPanel: vi.fn(),
}))

// Dock 本体在 CustomizePreviewDock 自己的测试覆盖；这里只需要「关闭」出口。
vi.mock('./CustomizePreviewDock', () => ({
  CustomizePreviewDock: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="customize-dialog">
      <button onClick={onClose}>关闭</button>
    </div>
  ),
}))

function renderSection() {
  return render(
    <PreviewPanelSection
      jobId="job-1"
      workspaceId="ws1"
      fallback={<div data-testid="generic-fallback">通用产物预览</div>}
    />,
    { wrapper: TestQueryProvider }
  )
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockFetchPublished.mockReset()
  mockFetchPublished.mockResolvedValue(null)
  mockFetchState.mockReset()
  mockFetchState.mockResolvedValue({
    published: null,
    draft: null,
  } satisfies PreviewPanelState)
  act(() => {
    useAuthStore.setState({ user: { role: 'admin' } as never })
  })
})

afterEach(() => {
  vi.useRealTimers()
  act(() => {
    useAuthStore.setState({ user: null })
  })
})

describe('PreviewPanelSection 治理面状态轮询档位（#965）', () => {
  it('对话关着 30s、开着 3s、关上后回落 30s', async () => {
    renderSection()
    await advance(0)
    expect(mockFetchState).toHaveBeenCalledTimes(1)

    // 对话关着：3s 档已关闭，30s 才拉一次。
    await advance(3_100)
    expect(mockFetchState).toHaveBeenCalledTimes(1)
    await advance(27_000)
    expect(mockFetchState).toHaveBeenCalledTimes(2)

    // 打开定制对话：立即刷新一次，并恢复 3s（改一版看一版）。
    fireEvent.click(screen.getByRole('button', { name: '定制预览' }))
    await advance(0)
    expect(mockFetchState).toHaveBeenCalledTimes(3)
    await advance(3_100)
    const afterOpen = mockFetchState.mock.calls.length
    expect(afterOpen).toBe(4)
    await advance(3_000)
    expect(mockFetchState.mock.calls.length).toBe(afterOpen + 1)

    // 关闭对话：回落 30s。
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    const afterClose = mockFetchState.mock.calls.length
    await advance(10_000)
    expect(mockFetchState.mock.calls.length).toBe(afterClose)
    await advance(20_100)
    expect(mockFetchState.mock.calls.length).toBe(afterClose + 1)
  })

  it('非 admin 不发治理面轮询', async () => {
    act(() => {
      useAuthStore.setState({ user: { role: 'member' } as never })
    })
    renderSection()
    await advance(60_000)
    expect(mockFetchState).not.toHaveBeenCalled()
  })
})
