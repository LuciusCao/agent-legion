/**
 * 产物预览渲染器：每个 PreviewKind 一个组件，props 统一为
 * { jobId, name, version }。文本类经 TanStack Query 取 artifact 文本
 * （版本失效机制沿用 producer-node 状态）；媒体类直接用同源 raw URL
 * 作 src，由浏览器流式加载。
 *
 * 安全约定（与后端 raw 端点白名单对齐）：
 * - html 走 RichText 的 sanitizeHtml 白名单（http(s)-only src）；
 * - markdown 走 renderMarkdownHtml（marked + DOMPurify）；
 * - svg 一律按 text 渲染源码，不经渲染引擎。
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Chip } from '@mui/material'
import { RichText } from '../RichText'
import { JsonTree } from '../JsonTree'
import { jobArtifactRawUrl } from '../../api/jobsApi'
import { fetchJobArtifactText } from '../../api/jobArtifactText'
import { queryKeys } from '../../lib/queryKeys'
import { artifactVersion } from '../../lib/jobArtifactVersions'
import { renderMarkdownHtml } from '../../lib/markdownHtml'
import { tryParseJson } from '../../lib/parsers'
import { toErrorMessage } from '../../lib/queryError'
import type { JobDetail } from '../../types/jobTypes'
import styles from './previewRenderers.module.css'

export interface PreviewRendererProps {
  jobId: string
  name: string
  /** jobDetail 查询数据（用于版本失效），可空——面板未加载完成时渲染骨架。 */
  detail: JobDetail | null
}

/** 文本截断阈值：>512KB 只展示前段，避免一次性挂载巨型 DOM。 */
const TEXT_PREVIEW_LIMIT = 512 * 1024

interface TextQueryResult {
  content: string
  truncated: boolean
  total: number
  loading: boolean
  error: string
}

function useArtifactText(
  jobId: string,
  name: string,
  detail: JobDetail | null
): TextQueryResult {
  const version = artifactVersion(detail, name)
  const query = useQuery({
    queryKey: queryKeys.jobArtifactText(jobId, name, version),
    // 有界读取：Range 只拉前 TEXT_PREVIEW_LIMIT+1 字节，大文本产物不再
    // 全量下载后客户端截断（codex P2 on #248）。
    queryFn: () => fetchJobArtifactText(jobId, name, TEXT_PREVIEW_LIMIT),
    enabled: Boolean(detail),
  })
  return {
    content: query.data?.content ?? '',
    truncated: query.data?.truncated ?? false,
    total: query.data?.total ?? 0,
    loading: query.isPending,
    error: query.error ? toErrorMessage(query.error) : '',
  }
}

function TruncationChip({ total }: { total: number }) {
  return (
    <Chip
      label={`已截断（${total.toLocaleString()} 字符）`}
      size="small"
      variant="outlined"
      sx={{ mb: 1 }}
    />
  )
}

function TextBody({
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

export function JsonPreview({ jobId, name, detail }: PreviewRendererProps) {
  const { content, truncated, total, loading, error } = useArtifactText(
    jobId,
    name,
    detail
  )
  if (loading) return <p className={styles.loading}>加载中...</p>
  if (error) return <p className={styles.error}>{error}</p>
  // 超限 JSON 不进 JsonTree：浅层大数组的展开 DOM 同样无界。
  if (truncated) {
    return <TextBody content={content} truncated={truncated} total={total} />
  }
  const parsed = tryParseJson(content)
  if (parsed === null) {
    // .json 但解析失败：按原文展示而不是空白。
    return <TextBody content={content} truncated={truncated} total={total} />
  }
  return <JsonTree data={parsed} />
}

export function MarkdownPreview({ jobId, name, detail }: PreviewRendererProps) {
  const { content, truncated, total, loading, error } = useArtifactText(
    jobId,
    name,
    detail
  )
  // 截断在渲染管线之前：多 MB 的 .md 全量 mount 会拖垮页面。
  const html = useMemo(
    () => (content ? renderMarkdownHtml(content) : ''),
    [content]
  )
  if (loading) return <p className={styles.loading}>加载中...</p>
  if (error) return <p className={styles.error}>{error}</p>
  return (
    <div>
      {truncated && <TruncationChip total={total} />}
      <div
        className={styles.markdownBody}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  )
}

export function RichTextPreview({ jobId, name, detail }: PreviewRendererProps) {
  const { content, truncated, total, loading, error } = useArtifactText(
    jobId,
    name,
    detail
  )
  if (loading) return <p className={styles.loading}>加载中...</p>
  if (error) return <p className={styles.error}>{error}</p>
  return (
    <div>
      {truncated && <TruncationChip total={total} />}
      <RichText mode="block">{content}</RichText>
    </div>
  )
}

export function TextPreview({ jobId, name, detail }: PreviewRendererProps) {
  const { content, truncated, total, loading, error } = useArtifactText(
    jobId,
    name,
    detail
  )
  if (loading) return <p className={styles.loading}>加载中...</p>
  if (error) return <p className={styles.error}>{error}</p>
  return <TextBody content={content} truncated={truncated} total={total} />
}

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
