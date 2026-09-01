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
 * - **出站网络由 CSP 钉死**（codex P1）：sandbox 的 allow-scripts 只隔离
 *   源与 DOM，不阻 fetch/sendBeacon/<img> 等外联——恶意 bundle 可先经桥
 *   读当前任务数据再发往任意外部地址。宿主在 <head> 注入 CSP meta（bundle
 *   无法移除：脚本能改的只有自己文档里的其他节点，注入发生在解析前），
 *   default-src 'none' + script/style 'unsafe-inline'（单文件 bundle 的本体
 *   就是 inline 脚本/样式）+ img-src data:（图表常见模式）+ connect-src 限
 *   平台 origin。connect-src 不能写 'self'：opaque origin 下 self 解析为
 *   null，会退化成全禁（连平台资源都加载不了）。
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

/** 出站网络红线（见文件头）：只放行平台 origin 与 inline/data 资源。 */
const PANEL_CSP = [
  "default-src 'none'",
  // 单文件 bundle 的脚本/样式本体就是 inline 的；katex 等平台资源按
  // init.assets 的绝对 URL 加载（script-src/style-src 需放行同源 URL）。
  "script-src 'unsafe-inline' 'self'",
  "style-src 'unsafe-inline' 'self'",
  'img-src data:',
  // 面板经桥取数，不需要任何 XHR/fetch；connect-src 收紧到平台 origin，
  // 堵死 fetch/sendBeacon 外传通道。'self' 在 opaque origin 下为 null，
  // 用注入时的绝对 origin。
  `connect-src ${typeof window === 'undefined' ? '' : window.location.origin}`,
  "form-action 'none'",
]
  .filter(Boolean)
  .join('; ')

/**
 * 把 CSP meta 注入 bundle 文档的 <head> 顶部。
 *
 * 不用 iframe 的 csp 属性（浏览器支持参差）也不依赖清单改造：srcDoc 解析
 * 前注入即生效。bundle 自带的 <meta http-equiv="Content-Security-Policy">
 * 若存在只会更严（多个 policy 取交集），不会松动宿主策略。
 */
function injectCsp(html: string): string {
  const meta = `<meta http-equiv="Content-Security-Policy" content="${PANEL_CSP}">`
  const headOpen = /<head(\s[^>]*)?>/i.exec(html)
  if (headOpen) {
    const at = headOpen.index + headOpen[0].length
    return `${html.slice(0, at)}${meta}${html.slice(at)}`
  }
  // 无 <head> 的文档：浏览器会隐式建一个，显式补在 <html> 后保证可见。
  const htmlOpen = /<html(\s[^>]*)?>/i.exec(html)
  if (htmlOpen) {
    const at = htmlOpen.index + htmlOpen[0].length
    return `${html.slice(0, at)}<head>${meta}</head>${html.slice(at)}`
  }
  // 非法文档（validate_panel_html 之外的草稿兜底）：头部前置仍先于正文脚本。
  return `${meta}${html}`
}

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

  // CSP 注入结果随 bundle 变化重算（srcDoc 导航见 PreviewPanelSection 的 key）。
  const framedHtml = useMemo(() => injectCsp(html), [html])

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
        // 安全红线见文件头注释：allow-scripts 可授，allow-same-origin 永不授；
        // 出站网络由注入的 CSP meta 钉死（见 injectCsp）。
        sandbox="allow-scripts"
        srcDoc={framedHtml}
        style={{ height }}
        onLoad={() => setLoading(false)}
      />
    </div>
  )
}
