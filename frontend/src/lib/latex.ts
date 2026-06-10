export function decodeHtmlEntities(raw: string): string {
  const textarea = document.createElement('textarea')
  textarea.innerHTML = raw
  return textarea.value
}
