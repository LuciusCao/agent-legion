/**
 * readArtifactBytes 的响应正文流式读取与超限错误（#1146；#1178 codex 复审
 * P2 从 jobArtifactBytes.ts 拆出——预算纪律）。
 */
export class ArtifactTooLargeError extends Error {
  constructor(
    readonly sizeBytes: number,
    readonly maxBytes: number
  ) {
    super(
      `artifact bytes ${sizeBytes} exceed readArtifactBytes limit ${maxBytes}`
    )
    this.name = 'ArtifactTooLargeError'
  }
}

/**
 * 流式读取响应正文，累计字节一超过 maxBytes 立即取消流并抛
 * ArtifactTooLargeError——不依赖 Content-Length（可能缺失或是 gzip 后的
 * 压缩长度），也绝不把超限正文完整分配进内存。无流式 API 的环境（测试
 * mock / 老引擎）回落整体读 + 事后复核。
 */
export async function readBodyWithLimit(
  response: Response,
  maxBytes: number
): Promise<ArrayBuffer> {
  const body = response.body
  if (!body) {
    const bytes = await response.arrayBuffer()
    if (bytes.byteLength > maxBytes) {
      throw new ArtifactTooLargeError(bytes.byteLength, maxBytes)
    }
    return bytes
  }
  const reader = body.getReader()
  const chunks: Uint8Array[] = []
  let total = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    total += value.byteLength
    if (total > maxBytes) {
      // 报告的 sizeBytes 是「已读到即超限」的下界，真实大小不再重要。
      await reader.cancel()
      throw new ArtifactTooLargeError(total, maxBytes)
    }
    chunks.push(value)
  }
  const bytes = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  return bytes.buffer
}
