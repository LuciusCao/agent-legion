/**
 * 文本类产物预览的有界读取：raw 端点本地（FileResponse）与对象存储
 * （ranged read → 206）两分支都支持 Range，只拉前 maxBytes 字节，避免
 * 打开含大型文本产物的 job 时全量下载再客户端截断。总数从
 * Content-Range 解析；服务端回 200（无区间）时按全文处理，客户端仍按
 * maxBytes 截断兜底。
 */

import { jobArtifactRawUrl } from './jobsApi'

export interface ArtifactTextPreview {
  /** 展示用文本（已按 maxBytes 截断）。 */
  content: string
  truncated: boolean
  /** 文件总字节数（未知时退化为已读长度）。 */
  total: number
}

export function jobArtifactTextPreviewOf(
  text: string,
  contentRange: string | null,
  maxBytes: number
): ArtifactTextPreview {
  const total =
    Number(/bytes \d+-\d+\/(\d+)/.exec(contentRange ?? '')?.[1]) || text.length
  return {
    content: text.slice(0, maxBytes),
    truncated: total > maxBytes + 1 || text.length > maxBytes,
    total,
  }
}

export async function fetchJobArtifactText(
  jobId: string,
  artifactName: string,
  maxBytes: number
): Promise<ArtifactTextPreview> {
  const response = await fetch(jobArtifactRawUrl(jobId, artifactName), {
    headers: { Range: `bytes=0-${maxBytes}` },
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`)
  }
  return jobArtifactTextPreviewOf(
    await response.text(),
    response.headers.get('Content-Range'),
    maxBytes
  )
}
