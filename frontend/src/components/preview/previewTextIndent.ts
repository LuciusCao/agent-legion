/**
 * codex P2（#604）：解析失败/截断 JSON 的括号深度缩进（previewTextBodies
 * 的姊妹件——文件预算纪律）。单趟、字符串感知：{ [ 之后换行进一层，
 * } ] , 之前换行；字符串字面量内的括号/逗号原样保留（配对引号扫描，
 * 找不到配对吞到文末，与 lexJsonish 同纪律、零回溯）。输出与输入的
 * token 序列等价（textContent 语义不变，仅空白布局变化）。
 */

export function indentJsonish(raw: string): string {
  const INDENT = '  '
  let depth = 0
  let out = ''
  let i = 0
  const n = raw.length
  const lineStart = () => INDENT.repeat(Math.max(0, depth))
  const pushBreak = () => {
    if (out.length > 0 && !out.endsWith('\n')) out += '\n'
    out += lineStart()
  }
  while (i < n) {
    const ch = raw[i]
    if (ch === '"') {
      let j = i + 1
      while (j < n) {
        if (raw[j] === '\\') j += 2
        else if (raw[j] === '"') break
        else j += 1
      }
      const end = j < n ? j + 1 : n
      out += raw.slice(i, end)
      i = end
      continue
    }
    if (ch === '{' || ch === '[') {
      out += ch
      depth += 1
      // 括号后还有内容（非紧邻闭合）才换行，空对象/数组保持 {} 内联。
      const rest = raw.slice(i + 1).trimStart()
      if (rest && rest[0] !== '}' && rest[0] !== ']') pushBreak()
      i += 1
      continue
    }
    if (ch === '}' || ch === ']') {
      depth -= 1
      pushBreak()
      out += ch
      i += 1
      continue
    }
    if (ch === ',') {
      out += ch
      pushBreak()
      i += 1
      continue
    }
    if (/\s/.test(ch)) {
      // 折叠字符串外的白空格为单个空格（保留 token 间最小间隔）。
      if (out.length > 0 && !out.endsWith('\n') && !/\s/.test(out.slice(-1))) {
        out += ' '
      }
      i += 1
      continue
    }
    out += ch
    i += 1
  }
  return out
}
