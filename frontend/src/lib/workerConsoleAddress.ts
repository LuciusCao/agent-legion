/** Navigation boundary for deployment addresses and untrusted Worker labels. */
export function safeWorkerConsoleAddress(value: unknown): string {
  if (
    typeof value !== 'string' ||
    !/^https?:\/\/[^/\\?#]+/i.test(value) ||
    /[\s\\]/u.test(value) ||
    [...value].some(
      (char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127
    )
  )
    return ''
  try {
    const url = new URL(value)
    return url.hostname && !url.username && !url.password ? value : ''
  } catch {
    return ''
  }
}
