/**
 * PreviewKind → 渲染器注册表（materialIconRegistry 的 Record 模式）。
 * 分类在 lib/previewKind.ts；渲染器实现在 ./previewRenderers。
 * 未注册的 kind 一律落到 TextPreview 兜底——预览面板永不白屏。
 */
import type { ComponentType } from 'react'
import type { PreviewKind } from '../../lib/previewKind'
import type { PreviewRendererProps } from './previewRenderers'
import {
  AudioPreview,
  ImagePreview,
  PdfPreview,
  VideoPreview,
} from './previewMediaRenderers'
import {
  JsonPreview,
  MarkdownPreview,
  RichTextPreview,
  TextPreview,
} from './previewRenderers'

export const PREVIEW_RENDERERS: Record<
  PreviewKind,
  ComponentType<PreviewRendererProps>
> = {
  json: JsonPreview,
  markdown: MarkdownPreview,
  richtext: RichTextPreview,
  image: ImagePreview,
  video: VideoPreview,
  audio: AudioPreview,
  pdf: PdfPreview,
  text: TextPreview,
}

export function resolvePreviewRenderer(
  kind: PreviewKind
): ComponentType<PreviewRendererProps> {
  return PREVIEW_RENDERERS[kind] ?? TextPreview
}
