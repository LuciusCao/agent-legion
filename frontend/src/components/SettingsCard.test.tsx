import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SettingsCard } from './SettingsCard'

describe('SettingsCard', () => {
  it('renders title and icon', () => {
    render(
      <SettingsCard icon="📡" title="资源连接">
        content
      </SettingsCard>
    )
    expect(screen.getByText('资源连接')).toBeInTheDocument()
    expect(screen.getByText('📡')).toBeInTheDocument()
    expect(screen.queryByText('content')).not.toBeInTheDocument()
  })

  it('expands and collapses on header click', () => {
    render(
      <SettingsCard icon="📡" title="资源连接" defaultExpanded={false}>
        content
      </SettingsCard>
    )
    const header = screen.getByTestId('settings-card-header')
    fireEvent.click(header)
    expect(screen.getByText('content')).toBeInTheDocument()
    fireEvent.click(header)
    expect(screen.queryByText('content')).not.toBeInTheDocument()
  })

  it('toggles expand/collapse on Enter and Space key', () => {
    render(
      <SettingsCard icon="📡" title="资源连接" defaultExpanded={false}>
        content
      </SettingsCard>
    )
    const header = screen.getByTestId('settings-card-header')
    fireEvent.keyDown(header, { key: 'Enter' })
    expect(screen.getByText('content')).toBeInTheDocument()
    fireEvent.keyDown(header, { key: ' ' })
    expect(screen.queryByText('content')).not.toBeInTheDocument()
  })

  it('shows status pill when provided', () => {
    render(
      <SettingsCard icon="📡" title="T" status={<span>ok</span>}>
        c
      </SettingsCard>
    )
    expect(screen.getByText('ok')).toBeInTheDocument()
  })
})
