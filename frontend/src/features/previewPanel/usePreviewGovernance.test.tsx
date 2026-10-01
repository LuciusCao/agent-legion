/**
 * usePreviewGovernance 的 workspace 隔离测试（codex P2 on #796）：
 * react-router 复用实例跨 workspace 导航时——
 * - A 的 actionError / pending 不泄漏到 B 的头部（渲染期按键过滤）；
 * - A 的迟到失败结果不写入 B（写入携带发起时 workspaceId 快照）。
 * mutation 层（usePublishPreviewPanel/useArchivePreviewPanel）按模块 mock。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { usePreviewGovernance } from './usePreviewGovernance'

const mockPublishMutate = vi.fn()
const mockArchiveMutate = vi.fn()

vi.mock('./usePreviewPanel', () => ({
  usePublishPreviewPanel: () => ({ mutateAsync: mockPublishMutate }),
  useArchivePreviewPanel: () => ({ mutateAsync: mockArchiveMutate }),
}))

function Probe({ workspaceId }: { workspaceId: string }) {
  const gov = usePreviewGovernance(workspaceId)
  return (
    <div>
      <span data-testid="error">{gov.actionError ?? ''}</span>
      <span data-testid="publishing">{String(gov.publishing)}</span>
      <button type="button" onClick={gov.publish}>
        发布
      </button>
      <button type="button" onClick={gov.archive}>
        归档
      </button>
    </div>
  )
}

function renderProbe(workspaceId = 'ws1') {
  return render((<Probe workspaceId={workspaceId} />) as ReactElement)
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('usePreviewGovernance 的 workspace 隔离', () => {
  it('A 的 actionError 不泄漏到 B', async () => {
    mockPublishMutate.mockRejectedValue(new Error('发布失败：冲突'))
    const { rerender } = renderProbe('ws1')
    fireEvent.click(screen.getByRole('button', { name: '发布' }))
    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('发布失败：冲突')
    )

    rerender((<Probe workspaceId="ws2" />) as ReactElement)
    // 键不匹配：B 的头部看不到 A 的失败。
    expect(screen.getByTestId('error')).toHaveTextContent('')
    expect(screen.getByTestId('publishing')).toHaveTextContent('false')
  })

  it('A 的迟到失败不写进 B；pending 在切换后立刻不可见', async () => {
    // 挂起的 promise：切换 workspace 后才 reject（迟到响应）。
    let rejectLate!: (reason: Error) => void
    mockPublishMutate.mockReturnValue(
      new Promise((_, reject) => {
        rejectLate = reject
      })
    )
    const { rerender } = renderProbe('ws1')
    fireEvent.click(screen.getByRole('button', { name: '发布' }))
    await waitFor(() =>
      expect(screen.getByTestId('publishing')).toHaveTextContent('true')
    )

    // 切到 B：A 的在途 pending 立刻不可见。
    rerender((<Probe workspaceId="ws2" />) as ReactElement)
    expect(screen.getByTestId('publishing')).toHaveTextContent('false')

    // A 的迟到失败落地：写入带 ws1 快照，渲染期被过滤——B 无错误。
    rejectLate(new Error('迟到的失败'))
    await waitFor(() => expect(mockPublishMutate).toHaveBeenCalledTimes(1))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(screen.getByTestId('error')).toHaveTextContent('')

    // 切回 A：A 的错误如实呈现（按 workspace 记忆，不是丢弃）。
    rerender((<Probe workspaceId="ws1" />) as ReactElement)
    expect(screen.getByTestId('error')).toHaveTextContent('迟到的失败')
    expect(screen.getByTestId('publishing')).toHaveTextContent('false')
  })
})
