/** 极简 Python 着色（原型用，零依赖）：按位置左到右匹配注释 / 字符串 /
 * decorator / 数字 / 关键字，其余 plain。字符串优先于注释匹配，故字符串
 * 内的 `#` 不会被误判为注释；正式版再评估是否换 Prism 等成熟方案。 */

export type PythonTokenKind =
  | 'plain'
  | 'keyword'
  | 'string'
  | 'comment'
  | 'number'
  | 'decorator'

export type PythonToken = {
  text: string
  kind: PythonTokenKind
}

const KEYWORDS = new Set([
  'and',
  'as',
  'assert',
  'async',
  'await',
  'break',
  'class',
  'continue',
  'def',
  'del',
  'elif',
  'else',
  'except',
  'finally',
  'for',
  'from',
  'global',
  'if',
  'import',
  'in',
  'is',
  'lambda',
  'nonlocal',
  'not',
  'or',
  'pass',
  'raise',
  'return',
  'try',
  'while',
  'with',
  'yield',
  'None',
  'True',
  'False',
])

const TOKEN_RE =
  /(#.*$)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(@[A-Za-z_][\w.]*)|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)\b/gm

export function tokenizePython(code: string): PythonToken[] {
  const tokens: PythonToken[] = []
  let last = 0
  for (const match of code.matchAll(TOKEN_RE)) {
    const index = match.index
    if (index > last) {
      tokens.push({ text: code.slice(last, index), kind: 'plain' })
    }
    const [text, comment, str, decorator, number, ident] = match
    let kind: PythonTokenKind = 'plain'
    if (comment) kind = 'comment'
    else if (str) kind = 'string'
    else if (decorator) kind = 'decorator'
    else if (number) kind = 'number'
    else if (ident && KEYWORDS.has(ident)) kind = 'keyword'
    tokens.push({ text, kind })
    last = index + text.length
  }
  if (last < code.length) {
    tokens.push({ text: code.slice(last), kind: 'plain' })
  }
  return tokens
}

/** 把 token 流按换行切成分行 token，供带行号的渲染使用（每行可为空数组）。 */
export function splitTokensByLine(tokens: PythonToken[]): PythonToken[][] {
  const lines: PythonToken[][] = [[]]
  for (const token of tokens) {
    const parts = token.text.split('\n')
    parts.forEach((part, index) => {
      if (index > 0) lines.push([])
      if (part) lines[lines.length - 1].push({ text: part, kind: token.kind })
    })
  }
  return lines
}
