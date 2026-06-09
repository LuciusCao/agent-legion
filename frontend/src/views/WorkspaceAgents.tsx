import { AgentPanel } from '../components/AgentPanel'
import { useUiStore } from '../stores/uiStore'

type Props = {
  isVideoHive: boolean
}

export default function WorkspaceAgents({ isVideoHive }: Props) {
  const agents = useUiStore((state) => state.agents)

  if (isVideoHive) {
    return <AgentPanel />
  }

  const busy = agents.filter((agent) => agent.busy).length

  return (
    <div>
      <section className="card-outlined" style={{ padding: 16, marginBottom: 16 }}>
        <h3>全局 Agent 池</h3>
        <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
          当前 agent 池是全局资源，尚未按 workspace 隔离。busy {busy} / total {agents.length}
        </p>
      </section>

      {agents.length === 0 ? (
        <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>暂无可用 Agent</p>
      ) : (
        <md-list>
          {agents.map((agent) => (
            <md-list-item key={agent.id}>
              <div slot="headline">{agent.name || agent.id}</div>
              <div slot="supporting-text">
                {agent.busy ? 'busy' : 'idle'} · {agent.task_count}/{agent.max_tasks}
                {agent.current_title ? ` · ${agent.current_title}` : ''}
              </div>
            </md-list-item>
          ))}
        </md-list>
      )}
    </div>
  )
}
