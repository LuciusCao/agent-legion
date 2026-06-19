import { createTheme, ThemeOptions } from '@mui/material/styles'

/**
 * Neutral (black/white/gray) theme for MUI v6.
 * Mirrors the previous @material/web token palette so the UI stays visually consistent.
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
    fontFamily: 'Roboto, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  shape: {
    borderRadius: 12,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          borderRadius: 28,
        },
      },
    },
  },
}

export const theme = createTheme(themeOptions)

export const STATUS_COLORS = {
  running: { bg: '#dbeafe', text: '#1d4ed8', border: '#3b82f6' },
  completed: { bg: '#dcfce7', text: '#15803d', border: '#22c55e' },
  failed: { bg: '#fee2e2', text: '#b91c1c', border: '#ef4444' },
  partial: { bg: '#fef3c7', text: '#92400e', border: '#f59e0b' },
} as const
