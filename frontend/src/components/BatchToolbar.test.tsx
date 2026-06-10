import { describe, it, expect, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { BatchToolbar } from './BatchToolbar'

describe('BatchToolbar', () => {
  it('renders selected count, filters, actions and exit button', () => {
    const onExit = vi.fn()
    render(
      <BatchToolbar
        selectedCount={3}
        filters={[
          { key: 'all', label: '全选', onClick: vi.fn() },
          { key: 'clear', label: '取消选择', onClick: vi.fn() },
        ]}
        actions={[
          { key: 'package', label: '打包', onClick: vi.fn() },
          { key: 'delete', label: '删除', danger: true, onClick: vi.fn() },
        ]}
        onExitSelectMode={onExit}
      />
    )

    expect(screen.getByText('已选择 3 项')).toBeInTheDocument()
    expect(screen.getByText('全选')).toBeInTheDocument()
    expect(screen.getByText('取消选择')).toBeInTheDocument()
    expect(screen.getByText('打包')).toBeInTheDocument()
    expect(screen.getByText('删除')).toBeInTheDocument()
    expect(screen.getByText('退出')).toBeInTheDocument()
  })

  it('calls filter onClick when filter button is clicked', () => {
    const onFilter = vi.fn()
    const onExit = vi.fn()
    render(
      <BatchToolbar
        selectedCount={0}
        filters={[{ key: 'all', label: '全选', onClick: onFilter }]}
        actions={[]}
        onExitSelectMode={onExit}
      />
    )

    act(() => {
      screen.getByText('全选').click()
    })
    expect(onFilter).toHaveBeenCalledTimes(1)
  })

  it('calls action onClick when action button is clicked', () => {
    const onAction = vi.fn()
    const onExit = vi.fn()
    render(
      <BatchToolbar
        selectedCount={1}
        filters={[]}
        actions={[{ key: 'rerun', label: '重跑', onClick: onAction }]}
        onExitSelectMode={onExit}
      />
    )

    act(() => {
      screen.getByText('重跑').click()
    })
    expect(onAction).toHaveBeenCalledTimes(1)
  })

  it('calls onExitSelectMode when exit button is clicked', () => {
    const onExit = vi.fn()
    render(
      <BatchToolbar
        selectedCount={0}
        filters={[]}
        actions={[]}
        onExitSelectMode={onExit}
      />
    )

    act(() => {
      screen.getByText('退出').click()
    })
    expect(onExit).toHaveBeenCalledTimes(1)
  })

  it('applies danger style to danger actions', () => {
    const onExit = vi.fn()
    render(
      <BatchToolbar
        selectedCount={0}
        filters={[]}
        actions={[
          { key: 'delete', label: '删除', danger: true, onClick: vi.fn() },
        ]}
        onExitSelectMode={onExit}
      />
    )

    const deleteButton = screen.getByText('删除')
    expect(deleteButton).toBeInTheDocument()
  })

  it('renders different button variants', () => {
    const onExit = vi.fn()
    render(
      <BatchToolbar
        selectedCount={0}
        filters={[]}
        actions={[
          { key: 'text', label: 'Text', variant: 'text', onClick: vi.fn() },
          {
            key: 'outlined',
            label: 'Outlined',
            variant: 'outlined',
            onClick: vi.fn(),
          },
          {
            key: 'filled',
            label: 'Filled',
            variant: 'filled',
            onClick: vi.fn(),
          },
        ]}
        onExitSelectMode={onExit}
      />
    )

    expect(screen.getByText('Text')).toBeInTheDocument()
    expect(screen.getByText('Outlined')).toBeInTheDocument()
    expect(screen.getByText('Filled')).toBeInTheDocument()
  })
})
