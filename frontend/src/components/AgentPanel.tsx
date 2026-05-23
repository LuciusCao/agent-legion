import { useUiStore } from "../stores/uiStore";

export function AgentPanel() {
  const { agents } = useUiStore();
  if (agents.length === 0) return null;

  const busyCount = agents.filter((a) => a.busy).length;
  const idleCount = agents.length - busyCount;

  return (
    <md-outlined-card className="agent-panel">
      <div className="agent-summary">
        Agent 状态：共 {agents.length} 个，{busyCount} 个忙碌，{idleCount} 个空闲
      </div>
      <div className="agent-list">
        {agents.map((agent, i) => (
          <div key={i} className={`agent-card ${agent.busy ? "busy" : "idle"}`}>
            <span className="agent-dot" />
            <span>{agent.name}</span>
            <span className="agent-pill">{agent.busy ? "忙碌" : "空闲"}</span>
          </div>
        ))}
      </div>
    </md-outlined-card>
  );
}
