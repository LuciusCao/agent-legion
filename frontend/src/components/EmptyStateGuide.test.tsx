import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { EmptyStateGuide } from './EmptyStateGuide'
import styles from './EmptyStateGuide.module.css'

describe('EmptyStateGuide', () => {
  const steps = [
    {
      icon: 'settings',
      title: '配置资源',
      description: '绑定 CMS 与工作流所需资源。',
      unlocked: true,
      actionLabel: '去配置',
      onAction: vi.fn(),
    },
    {
      icon: 'add_task',
      title: '创建任务',
      description: '选择接入模式并批量创建任务。',
      unlocked: false,
      actionLabel: '创建',
      onAction: vi.fn(),
    },
  ]

  beforeEach(() => {
    steps.forEach((s) => s.onAction.mockReset())
  })

  it('renders rocket icon and title', () => {
    render(<EmptyStateGuide steps={steps} />)

    expect(screen.getByText('rocket_launch')).toBeInTheDocument()
    expect(screen.getByText('开始使用 Workspace')).toBeInTheDocument()
  })

  it('renders each step title and description', () => {
    render(<EmptyStateGuide steps={steps} />)

    expect(screen.getByText('配置资源')).toBeInTheDocument()
    expect(screen.getByText('绑定 CMS 与工作流所需资源。')).toBeInTheDocument()
    expect(screen.getByText('创建任务')).toBeInTheDocument()
    expect(screen.getByText('选择接入模式并批量创建任务。')).toBeInTheDocument()
  })

  it('unlocked step button is enabled and calls onAction', () => {
    render(<EmptyStateGuide steps={steps} />)

    const btn = screen.getByText('去配置')
    expect(btn).not.toBeDisabled()
    fireEvent.click(btn)
    expect(steps[0].onAction).toHaveBeenCalledTimes(1)
  })

  it('locked step button is disabled and does not call onAction', () => {
    render(<EmptyStateGuide steps={steps} />)

    const btn = screen.getByText('创建')
    expect(btn).toBeDisabled()
    fireEvent.click(btn)
    expect(steps[1].onAction).not.toHaveBeenCalled()
  })

  it('applies locked styling to locked steps', () => {
    const { container } = render(<EmptyStateGuide steps={steps} />)

    const cards = container.querySelectorAll('[data-step]')
    expect(cards[0]).not.toHaveClass(styles.locked)
    expect(cards[1]).toHaveClass(styles.locked)
  })
})
