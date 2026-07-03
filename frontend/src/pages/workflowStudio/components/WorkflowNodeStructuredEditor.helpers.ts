export function parseLines(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function formatLines(items: string[]): string {
  return items.join('\n')
}
