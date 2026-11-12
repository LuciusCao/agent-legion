/**
 * 桥注入面板的主题变量（--pp-*，面板 CSS 用 var(--pp-*) 跟随平台观感；
 * 从 PreviewPanelHost.tsx 拆出——预算纪律）。
 */
import { type Theme } from '@mui/material/styles'

export function buildPreviewThemeVariables(
  theme: Theme
): Record<string, string> {
  return {
    '--pp-bg': theme.palette.background.default,
    '--pp-surface': theme.palette.background.paper,
    '--pp-text': theme.palette.text.primary,
    '--pp-text-secondary': theme.palette.text.secondary,
    '--pp-accent': theme.palette.primary.main,
    '--pp-on-accent': theme.palette.primary.contrastText,
    '--pp-error': theme.palette.error.main,
    '--pp-border': theme.palette.divider,
    '--pp-radius': `${theme.shape.borderRadius * 2}px`,
    '--pp-font-family': theme.typography.fontFamily ?? 'sans-serif',
  }
}
