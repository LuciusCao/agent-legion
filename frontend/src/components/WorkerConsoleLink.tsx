import { MaterialIcon } from './MaterialIcon'
import styles from './WorkerConsoleLink.module.css'

export interface WorkerConsoleLinkProps {
  /** 控制台地址；空串时不渲染（由调用方给出纯文字说明）。 */
  url: string
  label?: string
  variant?: 'inline' | 'button'
}

/** 回环地址只能在 Worker 所在机器的浏览器里打开；title 里把这点说清。 */
export function isLoopbackUrl(url: string): boolean {
  try {
    const host = new URL(url).hostname
    return host === '127.0.0.1' || host === 'localhost' || host === '[::1]'
  } catch {
    return false
  }
}

/**
 * 「打开 Worker 控制台」入口：新标签页打开，不内嵌、不代理——Worker
 * 控制台的控制 token 只在回环绑定时内嵌进它自己的页面
 * （worker/service_bind.py），主控制台只负责把人送过去。
 */
export function WorkerConsoleLink({
  url,
  label = '打开 Worker 控制台',
  variant = 'inline',
}: WorkerConsoleLinkProps) {
  if (!url) return null
  const title = isLoopbackUrl(url)
    ? `${url}（本机地址：需在 Worker 所在机器的浏览器中打开）`
    : url
  return (
    <a
      className={variant === 'button' ? styles.button : styles.inline}
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      data-testid="worker-console-link"
    >
      <span>{label}</span>
      <MaterialIcon name="open_in_new" sx={{ fontSize: 14 }} />
    </a>
  )
}
