/**
 * 文本类预览的公共正文组件（#255 拆出 previewRenderers 以过架构文件
 * 预算）：截断提示 chip、纯文本 <pre>、JSON 兜底格式化视图。
 *
 * FormattedJsonBody（#255 方案 C）：超限或解析失败的 .json 进不了
 * JsonTree，但仍是结构化数据——至少缩进 2 空格排版后着色键名/字符串/
 * 数字，等宽展示。内容先经 HTML 转义再注入 span 标记（React 文本子节点
 * 的等价手动路径），用户数据不可能构成标签。
 */
import { useMemo } from 'react'
import { Chip } from '@mui/material'
import { tryParseJson } from '../../lib/parsers'
import styles from './previewRenderers.module.css'

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
    const text = parsed === null ? content : JSON.stringify(parsed, null, 2)
    const escaped = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
    // 词法级着色（无上下文文法，覆盖三种 token 已够可读性）：
    // "key" 后跟冒号 → 键名；其余 "..." → 字符串；裸数字 → 数字。
    return escaped.replace(
      /("(?:[^"\\]|\\.)*")(\s*:)?|\b(-?\d+(?:\.\d+)?)\b/g,
      (_match, str: string, colon: string | undefined, num: string) => {
        if (str !== undefined)
          return colon !== undefined
            ? `<span class="${styles.jsonKey}">${str}</span>${colon}`
            : `<span class="${styles.jsonString}">${str}</span>`
        return `<span class="${styles.jsonNumber}">${num}</span>`
      }
    )
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
