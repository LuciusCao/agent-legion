import { render, screen, act } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { Z_LAYERS } from '../lib/zLayers'
import { useUiStore } from '../stores/uiStore'
import { theme } from '../theme'
import Toast from './Toast'

describe('Toast', () => {
  it('renders nothing when toast is null', () => {
    useUiStore.setState({ toast: null })
    const { container } = render(<Toast />)
    expect(container.firstChild).toBeNull()
  })

  it('renders success toast message', () => {
    useUiStore.setState({ toast: { message: '操作成功', type: 'success' } })
    render(<Toast />)
    expect(screen.getByText('操作成功')).toBeInTheDocument()
  })

  it('renders error toast message', () => {
    useUiStore.setState({ toast: { message: '操作失败', type: 'error' } })
    render(<Toast />)
    expect(screen.getByText('操作失败')).toBeInTheDocument()
  })

  it('层级取全局刻度：压过 Studio 抽屉栈、低于 MUI Modal（#818）', () => {
    useUiStore.setState({ toast: { message: '保存成功', type: 'success' } })
    render(<Toast />)
    const zIndex = Number(screen.getByRole('status').style.zIndex)
    expect(zIndex).toBe(Z_LAYERS.toast)
    expect(zIndex).toBeGreaterThan(Z_LAYERS.studioDrawerMax)
    expect(zIndex).toBeLessThan(theme.zIndex.modal)
  })

  it('auto-dismisses after 3 seconds', () => {
    vi.useFakeTimers()
    useUiStore.setState({ toast: { message: '稍等', type: 'success' } })
    render(<Toast />)
    expect(screen.getByText('稍等')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(3000)
    })

    expect(screen.queryByText('稍等')).not.toBeInTheDocument()
    vi.useRealTimers()
  })
})
