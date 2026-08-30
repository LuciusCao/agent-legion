import { ToggleButton, ToggleButtonGroup } from '@mui/material'
import { JsonTree } from '../JsonTree'
import { tryParseJson } from '../../lib/parsers'
import { renderMarkdownHtml } from '../../lib/markdownHtml'
import styles from './ArtifactRenderedPreview.module.css'

export type ArtifactPreviewMode = 'rendered' | 'source'

/** 预览/源码 toggle shown for renderable (markdown/html) artifacts. */
export function ArtifactPreviewModeToggle({
  name,
  mode,
  onMode,
}: {
  name: string
  mode: ArtifactPreviewMode
  onMode: (mode: ArtifactPreviewMode) => void
}) {
  const kind = artifactPreviewKind(name)
  if (kind !== 'markdown' && kind !== 'html') return null
  return (
    <ToggleButtonGroup
      size="small"
      exclusive
      value={mode}
      onChange={(_e, next: ArtifactPreviewMode | null) => {
        if (next) onMode(next)
      }}
    >
      <ToggleButton value="rendered">预览</ToggleButton>
      <ToggleButton value="source">源码</ToggleButton>
    </ToggleButtonGroup>
  )
}

export type ArtifactPreviewKind = 'json' | 'markdown' | 'html' | 'text'

export function artifactPreviewKind(name: string): ArtifactPreviewKind {
  const lower = name.toLowerCase()
  if (lower.endsWith('.json')) return 'json'
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) return 'markdown'
  if (lower.endsWith('.html') || lower.endsWith('.htm')) return 'html'
  return 'text'
}

/** Dialog body: JSON tree, rendered markdown/html, or the raw source. */
export function ArtifactPreviewBody({
  name,
  content,
  mode,
  preClassName,
}: {
  name: string
  content: string
  mode: 'rendered' | 'source'
  preClassName: string
}) {
  const kind = artifactPreviewKind(name)
  const parsedJson = kind === 'json' ? tryParseJson(content) : null
  if (parsedJson !== null) return <JsonTree data={parsedJson} />
  if (mode === 'rendered' && (kind === 'markdown' || kind === 'html')) {
    return <ArtifactRenderedPreview kind={kind} name={name} content={content} />
  }
  return <pre className={preClassName}>{content}</pre>
}

/** Rendered view for markdown / html artifacts (审批人所见即所得预览). */
export function ArtifactRenderedPreview({
  kind,
  name,
  content,
}: {
  kind: 'markdown' | 'html'
  name: string
  content: string
}) {
  if (kind === 'markdown') {
    return (
      <div
        className={styles.markdown}
        // renderMarkdownHtml sanitizes through the shared sanitizeHtml
        // profile (http(s)-only links/images, structural tags only).
        dangerouslySetInnerHTML={{ __html: renderMarkdownHtml(content) }}
      />
    )
  }
  return (
    // Fully inert sandbox: no scripts, no same-origin. A reviewed artifact is
    // untrusted output — with allow-scripts an injected <script> could still
    // exfiltrate the artifact via no-cors fetch/image beacons the moment the
    // reviewer opens the preview. Interactive coursework runs in its real
    // runtime after approval; the preview stays static markup.
    <iframe
      className={styles.htmlFrame}
      title={name}
      sandbox=""
      srcDoc={content}
    />
  )
}
