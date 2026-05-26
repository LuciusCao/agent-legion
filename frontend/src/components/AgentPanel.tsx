import { useUiStore } from "../stores/uiStore";
import styles from "./AgentPanel.module.css";

export function AgentPanel() {
  const { agents } = useUiStore();
  const busyCount = agents.reduce((sum, a) => sum + a.task_count, 0);
  const maxCount = agents.reduce((sum, a) => sum + a.max_tasks, 0);

  return (
    <div className={`${styles.agentPanel} card-outlined`}>
      <div className={styles.agentSummary}>
        Agent 状态：共 {agents.length} 个，{busyCount}/{maxCount} 任务运行中
      </div>
      {agents.length === 0 ? (
        <div className="empty-state">暂无运行中的 Agent</div>
      ) : (
        <div className={styles.agentList}>
          {agents.map((agent, i) => (
            <div key={i} className={`${styles.agentCard} ${agent.busy ? styles.busy : styles.idle}`}>
              <span className={styles.agentDot} />
              <span>{agent.name}</span>
              <span className={styles.agentPill}>
                {agent.busy ? `忙碌 (${agent.task_count}/${agent.max_tasks})` : "空闲"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
