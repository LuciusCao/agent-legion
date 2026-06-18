/**
 * MD3 Neutral (black/white/gray) theme tokens for @material/web
 */
export const applyTheme = (): void => {
  const root = document.documentElement
  const tokens: Record<string, string> = {
    '--md-sys-color-primary': '#000000',
    '--md-sys-color-on-primary': '#ffffff',
    '--md-sys-color-primary-container': '#e2e2e5',
    '--md-sys-color-on-primary-container': '#1a1c1e',
    '--md-sys-color-secondary': '#5d5f61',
    '--md-sys-color-on-secondary': '#ffffff',
    '--md-sys-color-secondary-container': '#e2e2e5',
    '--md-sys-color-on-secondary-container': '#1a1c1e',
    '--md-sys-color-surface': '#fafafa',
    '--md-sys-color-surface-variant': '#e2e2e5',
    '--md-sys-color-surface-container-lowest': '#ffffff',
    '--md-sys-color-surface-container-low': '#ffffff',
    '--md-sys-color-surface-container': '#ffffff',
    '--md-sys-color-surface-container-high': '#ffffff',
    '--md-sys-color-surface-container-highest': '#ffffff',
    '--md-sys-color-on-surface': '#1a1c1e',
    '--md-sys-color-on-surface-variant': '#43474e',
    '--md-sys-color-error': '#ba1a1a',
    '--md-sys-color-on-error': '#ffffff',
    '--md-sys-color-error-container': '#ffdad6',
    '--md-sys-color-outline': '#73777f',
    '--md-sys-color-outline-variant': '#c3c6cf',
    '--md-ref-typeface-brand': 'Roboto, sans-serif',
    '--md-ref-typeface-plain': 'Roboto, sans-serif',
    '--md-sys-elevation-level1':
      '0px 1px 2px 0px rgba(0, 0, 0, 0.3), 0px 1px 3px 1px rgba(0, 0, 0, 0.15)',
    '--md-sys-elevation-level2':
      '0px 1px 2px 0px rgba(0, 0, 0, 0.3), 0px 2px 6px 2px rgba(0, 0, 0, 0.15)',
    '--md-sys-elevation-level3':
      '0px 1px 3px 0px rgba(0, 0, 0, 0.3), 0px 4px 8px 3px rgba(0, 0, 0, 0.15)',
    '--md-sys-elevation-level4':
      '0px 2px 3px 0px rgba(0, 0, 0, 0.3), 0px 6px 10px 4px rgba(0, 0, 0, 0.15)',
    '--md-sys-elevation-level5':
      '0px 4px 4px 0px rgba(0, 0, 0, 0.3), 0px 8px 12px 6px rgba(0, 0, 0, 0.15)',
  }
  Object.entries(tokens).forEach(([key, value]) => {
    root.style.setProperty(key, value)
  })
}

export const STATUS_COLORS = {
  running: { bg: '#dbeafe', text: '#1d4ed8', border: '#3b82f6' },
  completed: { bg: '#dcfce7', text: '#15803d', border: '#22c55e' },
  failed: { bg: '#fee2e2', text: '#b91c1c', border: '#ef4444' },
} as const
