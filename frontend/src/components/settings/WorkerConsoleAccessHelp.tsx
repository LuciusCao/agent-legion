import styles from './WorkerConsoleGuide.module.css'

/** Shared by initial setup and the freshly issued workspace Key guide. */
export function WorkerConsoleAccessHelp() {
  return (
    <details className={styles.hint}>
      <summary>Worker 控制台要求控制令牌？</summary>
      <p>
        控制令牌用于登录这台 Worker 的控制台，与本 workspace 的注册 Key
        不同。Docker 部署即使只在本机发布端口，也需要先完成此登录。
      </p>
      <p>在启动 Worker 的机器、项目根目录执行（默认 Host Compose）：</p>
      <code>
        docker compose -f deploy/compose.host.yaml exec worker cat
        {' /var/lib/agent-legion-worker-control/control_token'}
      </code>
      <p>
        使用独立 Worker Compose 时，将文件换成启动时使用的
        deploy/compose.worker.standalone.yaml 或 deploy/compose.worker.yaml，
        并保留相同的项目名及其他 Compose 参数。原生部署从 Worker 的 --state-dir
        目录读取 control_token；没有该机器访问权限时请联系其维护者。
      </p>
      <p>
        将控制令牌粘贴到 Worker 登录框，随后到「配置 → Workspace 访问」添加注册
        Key。控制令牌只用于该 Worker，勿放进控制台链接或注册标签。
      </p>
    </details>
  )
}
