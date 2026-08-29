/**
 * 产物预览的渲染原语分类：扩展名 → 封闭的 PreviewKind 枚举。
 *
 * 分类是纯函数（不含 IO），注册表在 components/preview/registry.tsx。
 * 安全边界与后端 raw 端点的白名单一致：.svg 强制按 text 渲染源码
 * （内联 svg 即脚本执行面），.html 走 RichText 的 sanitizeHtml 白名单。
 */

export type PreviewKind =
  | 'richtext'
  | 'markdown'
  | 'json'
  | 'image'
  | 'video'
  | 'audio'
  | 'pdf'
  | 'text'

const KIND_BY_EXTENSION: Record<string, PreviewKind> = {
  json: 'json',
  md: 'markdown',
  markdown: 'markdown',
  html: 'richtext',
  htm: 'richtext',
  txt: 'text',
  log: 'text',
  csv: 'text',
  png: 'image',
  jpg: 'image',
  jpeg: 'image',
  gif: 'image',
  webp: 'image',
  mp4: 'video',
  webm: 'video',
  mov: 'video',
  mp3: 'audio',
  wav: 'audio',
  m4a: 'audio',
  ogg: 'audio',
  pdf: 'pdf',
}

export const PREVIEW_KIND_LABELS: Record<PreviewKind, string> = {
  richtext: '富文本',
  markdown: 'Markdown',
  json: 'JSON',
  image: '图片',
  video: '视频',
  audio: '音频',
  pdf: 'PDF',
  text: '文本',
}

export function classifyArtifactPreview(name: string): PreviewKind {
  const dot = name.lastIndexOf('.')
  if (dot <= 0 || dot === name.length - 1) return 'text'
  const ext = name.slice(dot + 1).toLowerCase()
  return KIND_BY_EXTENSION[ext] ?? 'text'
}
