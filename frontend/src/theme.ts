/**
 * MD3 Cyan theme tokens for @material/web
 */
export const applyTheme = (): void => {
  const root = document.documentElement;
  const tokens: Record<string, string> = {
    "--md-sys-color-primary": "#00838f",
    "--md-sys-color-on-primary": "#ffffff",
    "--md-sys-color-primary-container": "#b2ebf2",
    "--md-sys-color-on-primary-container": "#002022",
    "--md-sys-color-secondary": "#4a6367",
    "--md-sys-color-on-secondary": "#ffffff",
    "--md-sys-color-secondary-container": "#cde7ec",
    "--md-sys-color-on-secondary-container": "#051f23",
    "--md-sys-color-surface": "#f7f9f9",
    "--md-sys-color-surface-variant": "#dee4e0",
    "--md-sys-color-on-surface": "#002022",
    "--md-sys-color-on-surface-variant": "#3f494b",
    "--md-sys-color-error": "#ba1a1a",
    "--md-sys-color-on-error": "#ffffff",
    "--md-sys-color-error-container": "#ffdad6",
    "--md-sys-color-outline": "#6f797a",
    "--md-sys-color-outline-variant": "#bec8ca",
    "--md-ref-typeface-brand": "Roboto, sans-serif",
    "--md-ref-typeface-plain": "Roboto, sans-serif",
  };
  Object.entries(tokens).forEach(([key, value]) => {
    root.style.setProperty(key, value);
  });
};
