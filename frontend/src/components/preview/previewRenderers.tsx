/**
 * 文本类产物预览渲染器：每个 PreviewKind 一个组件，props 统一为
 * { jobId, name, version }。经 TanStack Query 取 artifact 文本
 * （版本失效机制沿用 producer-node 状态，Range 有界读取见
 * api/jobArtifactText）；媒体类渲染器在 ./previewMediaRenderers。
 *
 * 安全约定（与后端 raw 端点白名单对齐）：
 * - html 走 RichText 的 sanitizeHtml 白名单（http(s)-only src）；
 * - markdown 走 renderMarkdownHtml（marked + DOMPurify）；
 * - svg 一律按 text 渲染源码，不经渲染引擎。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Chip } from '@mui/material'
import { RichText } from '../RichText'
import { JsonTree } from '../JsonTree'
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
