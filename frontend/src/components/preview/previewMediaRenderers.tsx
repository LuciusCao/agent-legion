/**
 * 媒体类产物预览渲染器（从 previewRenderers 拆出以守文件预算）：统一
 * 用同源 raw URL 作 src 由浏览器流式加载；useMediaRetry 的 epoch 同时
 * 驱动 remount key 与 src cache-bust。props 契约与文本渲染器一致
 * （./previewRenderers 的 PreviewRendererProps）。
 */
import { useState } from 'react'
import { jobArtifactRawUrl } from '../../api/jobsApi'
import { artifactVersion } from '../../lib/jobArtifactVersions'
import type { PreviewRendererProps } from './previewRenderers'
import styles from './previewRenderers.module.css'

/** 媒体重试状态：epoch 同时驱动 remount key 与 src cache-bust。 */
function useMediaRetry() {
  const [failed, setFailed] = useState(false)
  const [epoch, setEpoch] = useState(0)
  const retry = () => {
    setFailed(false)
    setEpoch((n) => n + 1)
  }
  return { failed, epoch, setFailed, retry }
}

/** 媒体加载失败（404/格式不支持）的占位 + raw 新窗口兜底。 */
function MediaError({
  jobId,
  name,
  onRetry,
}: {
  jobId: string
  name: string
  onRetry: () => void
}) {
  return (
    <div className={styles.mediaError}>
      <span className={styles.mediaErrorText}>媒体加载失败</span>
      <button
        type="button"
        className={styles.mediaErrorAction}
        onClick={onRetry}
      >
        重试
      </button>
      <a
        className={styles.mediaErrorAction}
        href={jobArtifactRawUrl(jobId, name)}
        target="_blank"
        rel="noreferrer"
      >
        新窗口打开
      </a>
    </div>
  )
}

export function ImagePreview({ jobId, name, detail }: PreviewRendererProps) {
  const { failed, epoch, setFailed, retry } = useMediaRetry()
  if (failed) return <MediaError jobId={jobId} name={name} onRetry={retry} />
  // v = artifact 版本（重跑覆盖同名产物时失效缓存）+ epoch（手动重试）。
  const src = `${jobArtifactRawUrl(jobId, name)}?v=${artifactVersion(detail, name)}&r=${epoch}`
  return (
    <img
      key={src}
      className={styles.mediaImage}
      src={src}
      alt={name}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  )
}

export function VideoPreview({ jobId, name, detail }: PreviewRendererProps) {
  const { failed, epoch, setFailed, retry } = useMediaRetry()
  if (failed) return <MediaError jobId={jobId} name={name} onRetry={retry} />
  const src = `${jobArtifactRawUrl(jobId, name)}?v=${artifactVersion(detail, name)}&r=${epoch}`
  return (
    <video
      key={src}
      className={styles.mediaVideo}
      src={src}
      controls
      preload="metadata"
      onError={() => setFailed(true)}
    />
  )
}

export function AudioPreview({ jobId, name, detail }: PreviewRendererProps) {
  const { failed, epoch, setFailed, retry } = useMediaRetry()
  if (failed) return <MediaError jobId={jobId} name={name} onRetry={retry} />
  const src = `${jobArtifactRawUrl(jobId, name)}?v=${artifactVersion(detail, name)}&r=${epoch}`
  return (
    <audio
      key={src}
      className={styles.mediaAudio}
      src={src}
      controls
      preload="metadata"
      onError={() => setFailed(true)}
    />
  )
}

export function PdfPreview({ jobId, name, detail }: PreviewRendererProps) {
  // sandbox：PDF 内嵌渲染但不给脚本/同源能力。iframe 没有可靠的 error 事件
  // （加载失败也触发 load），失败不可检测——新窗口/下载兜底常驻展示。
  // v = artifact 版本：重跑覆盖同名产物时失效缓存。
  const src = `${jobArtifactRawUrl(jobId, name)}?v=${artifactVersion(detail, name)}`
  return (
    <div>
      <iframe className={styles.mediaPdf} src={src} title={name} sandbox="" />
      <a
        className={styles.pdfOpenLink}
        href={src}
        target="_blank"
        rel="noreferrer"
      >
        新窗口打开
      </a>
    </div>
  )
}
