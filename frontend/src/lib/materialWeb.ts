export function getSelectedValue(event: Event): string {
  const custom = event as CustomEvent<{ value?: string }>
  if (custom.detail?.value !== undefined) {
    return custom.detail.value
  }
  return (event.target as HTMLSelectElement | null)?.value ?? ''
}
