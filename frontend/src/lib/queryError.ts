/** 把 useQuery/useQueries 的 error 映射为展示用字符串（无错误时为空串）。 */
export function toErrorMessage(error: unknown): string {
  if (!error) return ''
  return error instanceof Error ? error.message : String(error)
}
