import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SettingsCard } from './SettingsCard'

describe('SettingsCard', () => {
  it('renders title, icon, and content', () => {
    render(
      <SettingsCard icon="settings_remote" title="资源连接">
        content
      </SettingsCard>
    )
    expect(screen.getByText('资源连接')).toBeInTheDocument()
    expect(screen.getByText('settings_remote')).toBeInTheDocument()
    expect(screen.getByText('content')).toBeInTheDocument()
  })

  it('shows status pill when provided', () => {
    render(
      <SettingsCard icon="settings_remote" title="T" status={<span>ok</span>}>
        c
      </SettingsCard>
    )
    expect(screen.getByText('ok')).toBeInTheDocument()
  })
})
