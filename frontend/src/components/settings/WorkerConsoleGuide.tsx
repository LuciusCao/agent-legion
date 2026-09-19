import { useWorkerConsoleUrl } from '../../hooks/useWorkerConsoleUrl'
import { WorkerConsoleLink } from '../WorkerConsoleLink'
import styles from './WorkerConsoleGuide.module.css'

/**
 * 「Agent 与 Worker」区顶部的固定说明：Worker 控制台是什么、在哪台机器、
 * 接入三步、两个默认关闭的开关。主控制台此前只在文案里提到「Worker
 * 控制台」而没有入口，这里连同地址一起给出（useWorkerConsoleUrl）。
 */
export function WorkerConsoleGuide({ isAdmin }: { isAdmin: boolean }) {
  const consoleUrl = useWorkerConsoleUrl()
  return (
    <div className={styles.card} data-testid="worker-console-guide">
      <div className={styles.header}>
        <h3 className={styles.heading}>Worker 与 Worker 控制台</h3>
        <WorkerConsoleLink url={consoleUrl ?? ''} variant="button" />
      </div>
      <p className={styles.text}>
        Worker 是真正执行任务的机器进程。它自带一个独立网页「Worker
        控制台」，运行在 Worker 所在的机器上（
        {consoleUrl ? (
          <>
            当前配置地址 <code>{consoleUrl}</code>
          </>
        ) : (
          <>
            本机开发默认 <code>http://127.0.0.1:8789</code>
          </>
        )}
        ），与当前这个控制台不是同一个服务。
      </p>
      <p className={styles.subheading}>让一台 Worker 接入本 workspace</p>
      <ol className={styles.steps}>
        <li>
          {isAdmin
            ? '在下方「签发新 Key」为本 workspace 签发 Key，复制 token（明文只显示一次）。'
            : '请管理员在本页为本 workspace 签发 Key，并把 token 交给你。'}
        </li>
        <li>
          打开 Worker 控制台，进入「配置 → Workspace 访问」，粘贴 token
          添加；Worker 会自动重新注册。
        </li>
        <li>
          回到 Worker 控制台「概览」，点「开始领取」。几秒内它会出现在下方的
          Worker 列表并显示「在线」。
        </li>
      </ol>
      <p className={styles.notice}>
        两个默认关闭的开关：Worker 每次启动都不领取任务，要在 Worker
        控制台点「开始领取」；后端每次启动都把 workspace
        调度重置为暂停，要在顶栏把「已暂停」切成「运行中」。任务一直停在「等待中」时先查这两处。
      </p>
      {consoleUrl === '' && (
        <p className={styles.hint} data-testid="worker-console-unset">
          未配置 Worker 控制台地址：请在 Worker
          所在机器直接打开其控制台（本机开发默认
          http://127.0.0.1:8789），或由维护人员设置{' '}
          <code>AGENT_LEGION_WORKER_CONSOLE_URL</code>。
        </p>
      )}
    </div>
  )
}
