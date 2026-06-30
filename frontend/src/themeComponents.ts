import { ThemeOptions } from '@mui/material/styles'

/**
 * Component overrides that make MUI's defaults feel more like the original
 * Material Design spec: smaller radii, uppercase buttons, heavier shadows.
 */
export const themeComponents: NonNullable<ThemeOptions['components']> = {
  MuiButton: {
    styleOverrides: {
      root: {
        borderRadius: 2,
        textTransform: 'uppercase',
      },
    },
  },
  MuiCard: {
    styleOverrides: {
      root: {
        borderRadius: 4,
        boxShadow: '0 1px 2px rgba(0, 0, 0, 0.12)',
      },
    },
  },
  MuiDialog: {
    styleOverrides: {
      paper: {
        borderRadius: 4,
      },
    },
  },
  MuiDialogContent: {
    styleOverrides: {
      root: {
        '&&': {
          paddingTop: '16px',
        },
      },
    },
  },
  MuiOutlinedInput: {
    styleOverrides: {
      root: {
        borderRadius: 2,
      },
    },
  },
  MuiChip: {
    styleOverrides: {
      root: {
        borderRadius: 4,
      },
    },
  },
}
