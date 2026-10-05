import type { ResolvedTextItem } from './textItem'

/**
 * 与后端 run_text_items.text_item_filename 同契约的裸文件名校验（合法返回
 * null，否则返回提示文案）：不含路径分隔符、不以点开头、无控制字符、
 * 必须以 .md/.txt/.json 结尾（大小写不敏感；与后端 text_item_content_types
 * 的白名单同步，#813 起放开 .json）。前端先行拦截，避免界面把必然
 * 被后端 400 拒绝的输入显示为可提交。
 */
export function textItemFilenameError(name: string): string | null {
  if (name.includes('/') || name.includes('\\') || name.startsWith('.'))
    return '文件名不能含路径分隔符、不能以点开头'
  for (const char of name) {
    const code = char.codePointAt(0) ?? 0
    if (code < 32 || code === 127) return '文件名不能含控制字符'
    // 未配对 UTF-16 代理项：后端 encode('utf-8') 会拒绝（契约测试覆盖），
    // 前端正向迭代时孤代理项呈现为 0xD800-0xDFFF 区间的独立码点。
    if (code >= 0xd800 && code <= 0xdfff) return '文件名含无效 Unicode 字符'
  }
  const dot = name.lastIndexOf('.')
  const suffix = dot >= 0 ? name.slice(dot).toLowerCase() : ''
  if (suffix !== '.md' && suffix !== '.txt' && suffix !== '.json')
    return '文件名须以 .md、.txt 或 .json 结尾'
  return null
}

/**
 * 已改动内容（非空白、非未触碰模板）但文件名无效——混合提交时不计数会被
 * 静默丢弃，对话框必须阻塞提交（codex #911 P1）。
 */
export function isInvalidTouchedTextItem(item: ResolvedTextItem): boolean {
  return (
    !!item.filenameError &&
    item.content.trim().length > 0 &&
    !item.untouchedTemplate
  )
}
