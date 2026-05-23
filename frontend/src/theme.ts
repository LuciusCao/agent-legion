/**
 * MD3 Cyan theme tokens for @material/web
 */
export const applyTheme = (): void => {
  const root = document.documentElement;
  const tokens: Record<string, string> = {
    "--md-sys-color-primary": "#000000",
    "--md-sys-color-on-primary": "#ffffff",
    "--md-sys-color-primary-container": "#e2e2e5",
    "--md-sys-color-on-primary-container": "#1a1c1e",
    "--md-sys-color-secondary": "#5d5f61",
    "--md-sys-color-on-secondary": "#ffffff",
    "--md-sys-color-secondary-container": "#e2e2e5",
    "--md-sys-color-on-secondary-container": "#1a1c1e",
    "--md-sys-color-surface": "#fafafa",
    "--md-sys-color-surface-variant": "#e2e2e5",
    "--md-sys-color-on-surface": "#1a1c1e",
    "--md-sys-color-on-surface-variant": "#43474e",
    "--md-sys-color-error": "#ba1a1a",
    "--md-sys-color-on-error": "#ffffff",
    "--md-sys-color-error-container": "#ffdad6",
    "--md-sys-color-outline": "#73777f",
    "--md-sys-color-outline-variant": "#c3c6cf",
    "--md-ref-typeface-brand": "Roboto, sans-serif",
    "--md-ref-typeface-plain": "Roboto, sans-serif",
  };
  Object.entries(tokens).forEach(([key, value]) => {
    root.style.setProperty(key, value);
  });
};
