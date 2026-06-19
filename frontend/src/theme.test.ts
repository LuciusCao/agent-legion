import { describe, it, expect } from 'vitest'
import { theme } from './theme'

describe('theme', () => {
  it('uses a neutral primary color for the black/white/gray UI', () => {
    expect(theme.palette.primary.main).toBe('#000000')
    expect(theme.palette.primary.contrastText).toBe('#ffffff')
  })

  it('sets a light surface background so panels are not transparent', () => {
    expect(theme.palette.background.default).toBe('#fafafa')
    expect(theme.palette.background.paper).toBe('#ffffff')
  })

  it('keeps the Roboto font family', () => {
    expect(theme.typography.fontFamily).toContain('Roboto')
  })
})
