/** 节点代码的窄栏摘要（#770）：行数 + 顶层签名（def / async def / class），
 * 入口函数（紧随 `@entrypoint` 装饰器的 def）排在最前。只做行级文本扫描，
 * 不解析 Python——摘要是导航提示，完整代码一律进宽视图看。 */
export type NodeCodeSummary = {
  lineCount: number
  /** 顶层签名行（去掉行尾冒号），最多 MAX_SIGNATURES 条。 */
  signatures: string[]
  /** 超出展示上限的顶层签名数。 */
  hiddenCount: number
  /** 被 `@entrypoint` 装饰的签名（无则 null）。 */
  entrypoint: string | null
}

export const MAX_SIGNATURES = 3

const TOP_LEVEL_SIGNATURE = /^(?:async\s+def|def|class)\s+\w+/

export function summarizeNodeCode(code: string): NodeCodeSummary {
  const trimmed = code.replace(/\n+$/, '')
  if (!trimmed)
    return { lineCount: 0, signatures: [], hiddenCount: 0, entrypoint: null }
  const lines = trimmed.split('\n')
  const found: string[] = []
  let entrypoint: string | null = null
  let decoratedEntrypoint = false
  for (const raw of lines) {
    const line = raw.trimEnd()
    if (/^@entrypoint\b/.test(line)) {
      decoratedEntrypoint = true
      continue
    }
    if (TOP_LEVEL_SIGNATURE.test(line)) {
      const signature = line.replace(/:\s*(#.*)?$/, '')
      found.push(signature)
      if (decoratedEntrypoint && entrypoint === null) entrypoint = signature
    }
    // 装饰器只修饰紧随其后的定义（中间允许其它装饰器行）。
    if (!line.startsWith('@')) decoratedEntrypoint = false
  }
  const ordered = entrypoint
    ? [entrypoint, ...found.filter((item) => item !== entrypoint)]
    : found
  return {
    lineCount: lines.length,
    signatures: ordered.slice(0, MAX_SIGNATURES),
    hiddenCount: Math.max(0, ordered.length - MAX_SIGNATURES),
    entrypoint,
  }
}
