import { createTheme, ThemeOptions } from '@mui/material/styles'
import { themeComponents } from './themeComponents'

/**
 * Neutral (black/white/gray) theme for MUI v6.
 * Mirrors the previous Material Web token palette so the UI stays visually consistent.
 *
 * The shape is intentionally more angular than MUI's defaults to preserve the
 * structured, mechanical feel of the original Material Design spec.
 */
const themeOptions: ThemeOptions = {
  palette: {
    mode: 'light',
    primary: {
      main: '#000000',
      contrastText: '#ffffff',
    },
    secondary: {
      main: '#5d5f61',
      contrastText: '#ffffff',
    },
    error: {
      main: '#ba1a1a',
      contrastText: '#ffffff',
    },
    background: {
      default: '#fafafa',
      paper: '#ffffff',
    },
    text: {
      primary: '#1a1c1e',
      secondary: '#43474e',
    },
    divider: '#c3c6cf',
  },
  typography: {
    fontFamily:
      'Roboto, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h1: { fontWeight: 500 },
    h2: { fontWeight: 500 },
    h3: { fontWeight: 500 },
    h4: { fontWeight: 500 },
    h5: { fontWeight: 500 },
    h6: { fontWeight: 500 },
    button: { fontWeight: 500, letterSpacing: '0.05em' },
  },
  shape: {
    borderRadius: 2,
  },
  components: themeComponents,
}

export const theme = createTheme(themeOptions)

export const STATUS_COLORS = {
  running: { bg: '#dbeafe', text: '#1d4ed8', border: '#3b82f6' },
  completed: { bg: '#dcfce7', text: '#15803d', border: '#22c55e' },
  failed: { bg: '#fee2e2', text: '#b91c1c', border: '#ef4444' },
  partial: { bg: '#fef3c7', text: '#92400e', border: '#f59e0b' },
} as const
