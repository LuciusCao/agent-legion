/**
 * 文本类预览的公共正文组件（#255 拆出 previewRenderers 以过架构文件
 * 预算）：截断提示 chip、纯文本 <pre>、JSON 兜底格式化视图。
 *
 * FormattedJsonBody（#255 方案 C）：超限或解析失败的 .json 进不了
 * JsonTree——缩进排版（可解析走 stringify；失败走 indentJsonish 的
 * 括号深度缩进）后着色键名/字符串/数字。内容先经 HTML 转义再注入
 * span 标记，用户数据不可能构成标签。
 */
import { useMemo } from 'react'
import { Chip } from '@mui/material'
import { tryParseJson } from '../../lib/parsers'
import styles from './previewRenderers.module.css'
import { indentJsonish } from './previewTextIndent'

export function TruncationChip({ total }: { total: number }) {
  return (
    <Chip
      label={`已截断（${total.toLocaleString()} 字符）`}
      size="small"
      variant="outlined"
      sx={{ mb: 1 }}
    />
  )
}

export function TextBody({
  content,
  truncated,
  total,
}: {
  content: string
  truncated: boolean
  total: number
}) {
  return (
    <div>
      {truncated && <TruncationChip total={total} />}
      <pre className={styles.pre}>{content}</pre>
    </div>
  )
}

/**
 * JSON 兜底视图：超限或解析失败的 .json 的可读性下限。解析失败时仍
 * 原文展示——多数失败是尾逗号/截断这类局部语法问题，结构大体可读。
 */
export function FormattedJsonBody({
  content,
  truncated,
  total,
}: {
  content: string
  truncated: boolean
  total: number
}) {
  const formatted = useMemo(() => {
    const parsed = tryParseJson(content)
    // codex P2：解析失败的 JSON 也做括号深度缩进（indentJsonish，姊妹
    // 件）——不再只是着色的超长单行。
    const text =
      parsed === null ? indentJsonish(content) : JSON.stringify(parsed, null, 2)
    const escaped = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;') // 转义先于 span 注入
    // 着色契约见 lexJsonish（单趟手写扫描；不得用单条全局正则——
    // 未闭合字符串会 O(K·L) 回溯，实测 512KB 截断内容 10-120s 冻结）。
    return lexJsonish(escaped)
  }, [content])
  return (
    <div>
      {truncated && <TruncationChip total={total} />}
      <pre
        className={styles.pre}
        // 内容先经 HTML 转义再注入标记，用户数据不可能构成标签。
        dangerouslySetInnerHTML={{ __html: formatted }}
      />
    </div>
  )
}

/**
 * 单趟 JSONish 词法着色（审核 P1 的修复体）：光标只前进、零回溯。
 *
 * 输入是已 HTML 转义的文本（&amp;/&lt;/&gt;），引号与反斜杠不受转义
 * 影响。逐位置判定：
 * - `"`：向后找配对引号（跳过 `\"`）——找到则按「后跟冒号=键名/否则
 *   字符串」着色整段；找不到（截断）则整段按字符串着色到文末——不再
 *   从中间每个引号位重试（这正是旧正则 O(K·L) 冻结的根因）。
 * - 数字开头（- 或数字）：吞 [-0-9.eE+] 段着色为数字（`&` 已转义，
 *   不会误吞实体）。
 * - 其他：原样输出一个字符。
 */
function lexJsonish(escaped: string): string {
  let out = ''
  let i = 0
  const n = escaped.length
  while (i < n) {
    const ch = escaped[i]
    if (ch === '"') {
      let j = i + 1
      while (j < n) {
        if (escaped[j] === '\\') j += 2
        else if (escaped[j] === '"') break
        else j += 1
      }
      const closed = j < n
      const end = closed ? j + 1 : n
      const body = escaped.slice(i, end)
      const rest = escaped.slice(end)
      const colonMatch = /^\s*:/.exec(rest)
      const cls = colonMatch ? styles.jsonKey : styles.jsonString
      const colon = colonMatch ? colonMatch[0] : ''
      out += `<span class="${cls}">${body}</span>${colon}`
      i = end + colon.length
      continue
    }
    // 负号必须后跟数字（否则是普通连字符，如 not-json）；数字段只吞
    // [0-9.]——科学计数/符号留给词法边界外（着色是可读性下限）。
    const isDigit = (c: string) => c >= '0' && c <= '9'
    if (isDigit(ch) || (ch === '-' && isDigit(escaped[i + 1] ?? ''))) {
      let j = i + 1
      while (j < n && /[0-9.]/.test(escaped[j]!)) j += 1
      out += `<span class="${styles.jsonNumber}">${escaped.slice(i, j)}</span>`
      i = j
      continue
    }
    out += ch
    i += 1
  }
  return out
}
