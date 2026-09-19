import { WorkerConsoleLink } from '../WorkerConsoleLink'
import styles from './WorkerTokenNextSteps.module.css'

/**
 * 签发 Key 成功后的「下一步」三步指引：复制 token → Worker 控制台
 * 「配置 → Workspace 访问」粘贴 → 「开始领取」，并给出控制台入口
 * （WorkerTokensSection 体积预算已满，指引单独成文件）。
 */
export function WorkerTokenNextSteps({ consoleUrl }: { consoleUrl: string }) {
  return (
    <div className={styles.nextSteps} data-testid="created-token-next-steps">
      <p className={styles.nextStepsTitle}>
        下一步：让 Worker 接入本 workspace
      </p>
      <ol className={styles.nextStepsList}>
        <li>复制上方 token。</li>
        <li>
          打开 Worker 控制台，进入「配置 → Workspace 访问」，粘贴添加；Worker
          会自动重新注册。
        </li>
        <li>
          回到 Worker
          控制台「概览」，点「开始领取」。几秒内它会出现在下方「已注册
          Worker」列表。
        </li>
      </ol>
      <WorkerConsoleLink url={consoleUrl} variant="button" />
    </div>
  )
}
