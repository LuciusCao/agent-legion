/**
 * 预览面板桥 readArtifactBytes 的宿主侧字节读取（issue #1146）。
 *
 * 面板 iframe 是 opaque origin、无会话 cookie，媒体字节由宿主用自身会话
 * 经 raw 端点取回（同源 fetch 自动带 cookie，GET 免 CSRF），再经 postMessage
 * structured clone 把 ArrayBuffer 回传面板——不走 base64。安全语义与
 * readArtifact 完全一致：只回当前查看者已有权看到的数据，桥无写面。
 *
 * 缓存语义（#1178 codex 复审）：本方法承担「init 重发 → 面板重取」的重取
 * 通道，必须穿透 HTTP 缓存——同名产物重跑覆盖后字节已变而 URL 不变，
 * freshness window 内浏览器会直接复用旧响应（本地 FileResponse 带
 * ETag/Last-Modified），面板就继续播放重跑前的字节。fetch 固定
 * `cache: 'no-store'`：总是打到服务端（ETag 仍可协商省带宽，但 freshness
 * window 不再截流）；服务端 raw 路由侧为 manifest-first（对象存储权威
 * 副本优先于宿主 job_dir 缓存，#1178 codex 复审 P2），链路两端合起来
 * 保证重取到的是当前字节。版本参数形态留给后续（需要宿主 assets 携带
 * 产物版本号——内置媒体渲染器走 artifact version 查询参数的同一思路）。
 *
 * 内存护栏（#1178 codex 复审 P2）：读取前按 Content-Length 预检（对象
 * 存储流式分支可能不带该头，gzip 时声明的还是压缩后长度），读取走
 * `response.body` 流式累积、累计超限即 cancel（artifactByteStream.ts）——
 * 不能在 arrayBuffer() 全量分配后才复核（超限字节会完整落进宿主标签页
 * 内存）。媒体类型过滤（按 manifest/content-type 限定媒体类）留给后续：
 * raw 端点的 content-type 白名单已是服务端边界（非媒体一律
 * octet-stream+attachment），桥按 readArtifact 同语义放行任意产物名，
 * 上限护栏先行。
 */

import { jobArtifactRawUrl } from './jobsApi'
import { ArtifactTooLargeError, readBodyWithLimit } from './artifactByteStream'

export { ArtifactTooLargeError }

/** 单次 readArtifactBytes 允许进内存的字节上限（512 MiB）。 */
export const READ_ARTIFACT_BYTES_MAX_BYTES = 512 * 1024 * 1024

export interface ArtifactBytesResponse {
  name: string
  /** raw 端点按扩展名白名单映射的媒体类型（非媒体为 application/octet-stream）。 */
  mediaType: string
  /** 产物完整字节；面板用 URL.createObjectURL(new Blob([bytes])) 播放。 */
  bytes: ArrayBuffer
}

export async function fetchJobArtifactRawBytes(
  jobId: string,
  artifactName: string,
  maxBytes: number = READ_ARTIFACT_BYTES_MAX_BYTES
): Promise<ArtifactBytesResponse> {
  // no-store：见文件头「缓存语义」——重取通道必须穿透 freshness window，
  // 重跑后的同名产物不能从 HTTP 缓存里播出旧字节。
  const response = await fetch(jobArtifactRawUrl(jobId, artifactName), {
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`)
  }
  const declared = Number(response.headers.get('Content-Length'))
  if (Number.isFinite(declared) && declared > maxBytes) {
    await response.body?.cancel()
    throw new ArtifactTooLargeError(declared, maxBytes)
  }
  const bytes = await readBodyWithLimit(response, maxBytes)
  return {
    name: artifactName,
    mediaType:
      response.headers.get('Content-Type') ?? 'application/octet-stream',
    bytes,
  }
}
