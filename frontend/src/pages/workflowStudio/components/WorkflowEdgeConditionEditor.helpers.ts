export function parseEquals(raw: string): string | boolean | null {
  const trimmed = raw.trim()
  if (trimmed === 'true') return true
  if (trimmed === 'false') return false
  if (trimmed === 'null') return null
  return trimmed
}

export function formatEquals(value: unknown): string {
  if (value === undefined) return ''
  if (value === true) return 'true'
  if (value === false) return 'false'
  if (value === null) return 'null'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

export function coerceEquals(value: unknown): string | number | boolean | null {
  return (value ?? '') as string | number | boolean | null
}
