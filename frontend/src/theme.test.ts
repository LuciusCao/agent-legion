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

  it('uses an angular shape to match the original Material Design feel', () => {
    expect(theme.shape.borderRadius).toBe(2)
  })

  it('uses uppercase buttons for a structured, mechanical look', () => {
    const buttonStyle = theme.components?.MuiButton?.styleOverrides?.root as
      | Record<string, unknown>
      | undefined
    expect(buttonStyle?.textTransform).toBe('uppercase')
  })

  it('uses heavier headings so titles feel grounded', () => {
    expect(theme.typography.h1?.fontWeight).toBe(500)
    expect(theme.typography.h6?.fontWeight).toBe(500)
    expect(theme.typography.button?.fontWeight).toBe(500)
  })
})
