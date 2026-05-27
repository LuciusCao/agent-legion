import { useEffect } from "react";
import { useUiStore } from "../stores/uiStore";
import styles from "./AgentPanel.module.css";

export function AgentPanel() {
  const { agents, workerPaused, fetchWorkerStatus, setWorkerPaused, showToast } = useUiStore();
  const busyCount = agents.reduce((sum, a) => sum + a.task_count, 0);
  const maxCount = agents.reduce((sum, a) => sum + a.max_tasks, 0);

  useEffect(() => {
    fetchWorkerStatus().catch((err) => {
      const message = err instanceof Error ? err.message : String(err);
      showToast(`加载队列调度状态失败: ${message}`, "error");
    });
  }, [fetchWorkerStatus, showToast]);

  const handlePausedChange = async () => {
    const paused = !workerPaused;
    try {
      await setWorkerPaused(paused);
      showToast(paused ? "已暂停队列调度" : "已恢复队列调度", "success");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      showToast(`更新队列调度状态失败: ${message}`, "error");
    }
  };

  return (
    <div className={`${styles.agentPanel} card-outlined`}>
      <div className={styles.agentHeader}>
        <div className={styles.agentSummary}>
          Agent 状态：共 {agents.length} 个，{busyCount}/{maxCount} 任务运行中
        </div>
        <label className={styles.assignmentSwitch}>
          <span>{workerPaused ? "已暂停队列调度" : "队列调度中"}</span>
          <md-switch selected={!workerPaused || undefined} onClick={handlePausedChange} />
        </label>
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
