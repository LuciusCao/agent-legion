/**
 * 预览面板 iframe 宿主（issue #328）：把 workspace 的预览面板 bundle
 * （HTML+CSS+JS 单文件）渲染进左栏 content 面板，并提供只读 postMessage 桥。
 *
 * 威胁模型（与 components/artifact/ArtifactRenderedPreview.tsx 的静态产物
 * 预览刻意不同）：
 * - bundle 是 agent 起草、人工发布的**代码**，必须能跑脚本——所以沙箱授
 *   `allow-scripts`；ArtifactRenderedPreview 面对的是纯内容，授空沙箱。
 * - **永不授 `allow-same-origin`**：授了它，allow-scripts 的 bundle 就变成
 *   同源脚本——能携带会话 cookie 调平台全部 API、读宿主 DOM，多用户部署下
 *   等于会话接管。opaque origin 下面板拿不到 cookie/localStorage/宿主 DOM。
 * - 桥只暴露只读方法（listArtifacts/readArtifact/getJobDetail），返回的都是
 *   当前页面用户本来就有权看到的数据；写操作（发布/归档/改配置）不走桥。
 * - 消息鉴别：opaque origin 的 event.origin 恒为 "null"，不能用来鉴权——
 *   宿主钉住 event.source === iframe.contentWindow 再校验 source 标记；
 *   回包 postMessage(..., '*') 的目标窗口由 contentWindow 引用钉死，不会
 *   投递到其他窗口。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTheme, type Theme } from '@mui/material/styles'
import katexCssUrl from 'katex/dist/katex.min.css?url'
import katexJsUrl from 'katex/dist/katex.min.js?url'
import { fetchJobArtifact } from '../../api'
import { useJobDetailQuery } from '../../hooks/useJobDetailQuery'
import type { JobDetail } from '../../types/jobTypes'
import {
  isPanelToHostMessage,
  PREVIEW_HOST_SOURCE,
  type PreviewHostInitMessage,
} from './bridge'
import styles from './PreviewPanelHost.module.css'

const MIN_HEIGHT = 120
const MAX_HEIGHT = 6000
const DEFAULT_HEIGHT = 320

export interface PreviewPanelHostProps {
  jobId: string
  /** 完整 HTML 文档 bundle（已发布版本或草稿预览）。 */
  html: string
  title?: string
}

/** 桥注入的主题变量：面板 CSS 用 var(--pp-*) 跟随平台观感。 */
function buildThemeVariables(theme: Theme): Record<string, string> {
  return {
    '--pp-bg': theme.palette.background.default,
    '--pp-surface': theme.palette.background.paper,
    '--pp-text': theme.palette.text.primary,
    '--pp-text-secondary': theme.palette.text.secondary,
    '--pp-accent': theme.palette.primary.main,
    '--pp-on-accent': theme.palette.primary.contrastText,
    '--pp-error': theme.palette.error.main,
    '--pp-border': theme.palette.divider,
    '--pp-radius': `${theme.shape.borderRadius * 2}px`,
    '--pp-font-family': theme.typography.fontFamily ?? 'sans-serif',
  }
}

export function PreviewPanelHost({
  jobId,
  html,
  title,
}: PreviewPanelHostProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [loading, setLoading] = useState(true)
  const [height, setHeight] = useState<number>(DEFAULT_HEIGHT)
  const theme = useTheme()
  const { data: detail } = useJobDetailQuery(jobId)
  // ready 之后节点状态翻转（产物节点完成）→ 重发 init 触发面板重取数据
  // （替代 #11 时代面板内部的 artifact 重取；bundle 侧约定 init 即重渲染）。
  const readyRef = useRef(false)
  const nodeSignatureRef = useRef<string | null>(null)

  const initMessage = useMemo<PreviewHostInitMessage>(
    () => ({
      source: PREVIEW_HOST_SOURCE,
      type: 'init',
      jobId,
      theme: buildThemeVariables(theme),
      assets: {
        // bundle 可按需懒加载平台构建产物（LaTeX 等）；缺失时必须自行降级。
        katexCssUrl: new URL(katexCssUrl, window.location.origin).href,
        katexJsUrl: new URL(katexJsUrl, window.location.origin).href,
      },
    }),
    [jobId, theme]
  )

  useEffect(() => {
    const signature = (detail?.nodes ?? [])
      .map((node) => `${node.node_key}:${node.status}`)
      .join('|')
    if (!readyRef.current) {
      return
    }
    if (
      nodeSignatureRef.current !== null &&
      nodeSignatureRef.current !== signature
    ) {
      iframeRef.current?.contentWindow?.postMessage(initMessage, '*')
    }
    nodeSignatureRef.current = signature
  }, [detail, initMessage])

  useEffect(() => {
    function respond(
      id: number,
      ok: boolean,
      payload?: unknown,
      error?: string
    ) {
      iframeRef.current?.contentWindow?.postMessage(
        {
          source: PREVIEW_HOST_SOURCE,
          type: 'response',
          id,
          ok,
          ...(ok ? { payload } : { error: error ?? 'unknown error' }),
        },
        '*'
      )
    }
    async function handleRequest(
      id: number,
      method: string,
      params: { name?: string } | undefined,
      currentDetail: JobDetail | undefined
    ) {
      try {
        switch (method) {
          case 'listArtifacts':
            respond(id, true, currentDetail?.artifacts ?? [])
            return
          case 'getJobDetail':
            respond(id, true, currentDetail ?? null)
            return
          case 'readArtifact': {
            const name = params?.name
            if (!name) {
              respond(id, false, undefined, 'readArtifact requires params.name')
              return
            }
            respond(id, true, await fetchJobArtifact(jobId, name))
            return
          }
          default:
            respond(id, false, undefined, `unknown bridge method: ${method}`)
        }
      } catch (error) {
        respond(
          id,
          false,
          undefined,
          error instanceof Error ? error.message : String(error)
        )
      }
    }

    function onMessage(event: MessageEvent) {
      const frame = iframeRef.current
      if (!frame || event.source !== frame.contentWindow) return
      const data: unknown = event.data
      if (!isPanelToHostMessage(data)) return
      if (data.type === 'ready') {
        readyRef.current = true
        nodeSignatureRef.current = (detail?.nodes ?? [])
          .map((node) => `${node.node_key}:${node.status}`)
          .join('|')
        frame.contentWindow?.postMessage(initMessage, '*')
        return
      }
      if (data.type === 'resize') {
        setHeight(
          Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.round(data.height)))
        )
        return
      }
      void handleRequest(data.id, data.method, data.params, detail)
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [jobId, detail, initMessage])

  return (
    <div className={styles.wrapper} data-testid="preview-panel-host">
      {loading && <div className={styles.loading}>预览加载中…</div>}
      <iframe
        ref={iframeRef}
        className={styles.frame}
        title={title ?? '自定义预览面板'}
        // 安全红线见文件头注释：allow-scripts 可授，allow-same-origin 永不授。
        sandbox="allow-scripts"
        srcDoc={html}
        style={{ height }}
        onLoad={() => setLoading(false)}
      />
    </div>
  )
}
