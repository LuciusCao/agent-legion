import { presignMaterial, completeMaterial } from '../api/materialsApi'

/** 超过该体积的文件跳过浏览器端 sha256（presign 的 content_hash 可选）。 */
export const SHA256_MAX_BYTES = 64 * 1024 * 1024

/** 粘贴 ID 解析：按行拆分、去空白、去空行、保序去重。 */
export function parseRefIds(text: string): string[] {
  const seen = new Set<string>()
  const ids: string[] = []
  for (const line of text.split('\n')) {
    const id = line.trim()
    if (!id || seen.has(id)) continue
    seen.add(id)
    ids.push(id)
  }
  return ids
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(k)),
    units.length - 1
  )
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${units[i]}`
}

/** 预览分组：优先 MIME 主类型，退回扩展名，都没有归「其他」。 */
export function fileTypeGroup(filename: string, contentType: string): string {
  const main = contentType.split('/')[0]?.trim()
  if (main === 'image') return '图片'
  if (main === 'video') return '视频'
  if (main === 'audio') return '音频'
  if (main === 'text') return '文本'
  if (contentType === 'application/pdf') return 'PDF'
  const ext = filename.includes('.')
    ? filename.split('.').pop()!.toLowerCase()
    : ''
  return ext ? `.${ext}` : '其他'
}

export async function computeFileSha256(file: File): Promise<string | null> {
  if (file.size > SHA256_MAX_BYTES) return null
  if (typeof crypto === 'undefined' || !crypto.subtle) return null
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

export type UploadMaterialResult = {
  materialId: string
  deduplicated: boolean
}

/** 单文件上传协议：presign → PUT 直传对象存储 → complete。 */
export async function uploadMaterialFile(
  workspaceId: string,
  file: File,
  filename: string
): Promise<UploadMaterialResult> {
  const contentHash = (await computeFileSha256(file)) ?? undefined
  const presigned = await presignMaterial(workspaceId, {
    filename,
    size_bytes: file.size,
    content_type: file.type || '',
    ...(contentHash ? { content_hash: contentHash } : {}),
  })
  const materialId = presigned.material.id
  if (presigned.deduplicated) {
    return { materialId, deduplicated: true }
  }
  if (!presigned.upload_url) {
    throw new Error('后端未返回上传地址')
  }
  const response = await fetch(presigned.upload_url, {
    method: 'PUT',
    body: file,
  })
  if (!response.ok) {
    throw new Error(`直传失败 (HTTP ${response.status})`)
  }
  await completeMaterial(workspaceId, materialId)
  return { materialId, deduplicated: false }
}

/** 简单并发池：最多 concurrency 个 worker 同时消费队列。 */
export async function runWithConcurrency<T>(
  items: T[],
  concurrency: number,
  worker: (item: T) => Promise<void>
): Promise<void> {
  const queue = [...items]
  const lanes = Array.from(
    { length: Math.max(1, Math.min(concurrency, queue.length)) },
    async () => {
      for (;;) {
        const item = queue.shift()
        if (item === undefined) return
        await worker(item)
      }
    }
  )
  await Promise.all(lanes)
}
