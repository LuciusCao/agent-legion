// labelsFromText / numberField / NUMBER_DEFAULTS / formatExecution 是纯函数，由 app.test.mjs（node:test）直接 import；
// 因此本文件以 ES module 加载（见 index.html 的 script type="module"），DOM 访问做存在性守卫。
const hasDom = typeof document !== "undefined";
const form = hasDom ? document.querySelector("#config-form") : null;
const errorBox = hasDom ? document.querySelector("#form-error") : null;
const CONTROL_TOKEN = typeof window !== "undefined" ? window.__WORKER_CONTROL_TOKEN__ : undefined;
const TOKEN_MISSING = !CONTROL_TOKEN || CONTROL_TOKEN === "__WORKER_CONTROL_TOKEN__";
// 与 worker/config_store.py 的 _DEFAULTS 对齐：数字字段留空时回退到后端默认值。
export const NUMBER_DEFAULTS = { max_concurrency: 1, poll_interval_seconds: 2, heartbeat_interval_seconds: 15, shutdown_grace_seconds: 25 };

async function api(path, options = {}) {
  const headers = { Authorization: `Bearer ${CONTROL_TOKEN}`, ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json();
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return payload;
}

function setText(id, value) {
  document.querySelector(`#${id}`).textContent = value ?? "—";
}

function renderStatus(status) {
  const dot = document.querySelector("#status-dot");
  dot.className = `dot ${status.connected && status.worker_running ? "ok" : "warn"}`;
  setText("status-title", status.connected && status.worker_running ? "Worker 已登记并运行" : "Worker 尚未就绪");
  setText("status-detail", status.failed || status.bootstrap_error || status.connection_error || (status.configured ? "本地配置已加载" : "请先完成配置"));
  const processState = status.worker_running
    ? `运行中 · PID ${status.pid}`
    : status.failed
      ? "已失败"
      : status.next_restart_delay
        ? `等待自动重启（第 ${status.restart_count} 次）`
        : "未运行";
  setText("process-state", processState);
  setText("host-state", status.host_reachable ? "可达" : "不可达");
  setText("registered-state", status.registered ? `${status.host_worker.worker_id} · ${status.host_worker.name}` : "未登记");
  const scope = status.host_worker?.allowed_workspaces;
  setText("workspace-scope", scope ? (scope.length ? scope.join(", ") : "全部") : "—");
  setText("last-seen", status.host_worker?.last_seen_at || "—");
  renderExecutions(status.current_executions);
}

function fillForm(config) {
  for (const [key, value] of Object.entries(config)) {
    if (key === "runtimes") {
      form.querySelectorAll('[name="runtimes"]').forEach((input) => { input.checked = value.includes(input.value); });
    } else if (key === "labels") {
      form.elements.labels.value = Object.entries(value).map(([label, item]) => `${label}=${item}`).join("\n");
    } else if (form.elements[key]) {
      form.elements[key].value = value;
    }
  }
}

export function formatExecution(execution, now = Date.now()) {
  const started = Date.parse(execution.started_at);
  const elapsed = Number.isNaN(started) ? 0 : Math.max(0, Math.floor((now - started) / 1000));
  const minutes = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const seconds = String(elapsed % 60).padStart(2, "0");
  const label = [execution.agent_id, execution.node_key].filter(Boolean).join(" · ") || execution.execution_id || "unknown";
  return `${label} · ${execution.phase || "?"} · ${minutes}:${seconds}`;
}

function renderExecutions(executions) {
  const list = document.querySelector("#executions");
  list.textContent = "";
  if (!executions || executions.length === 0) {
    const idle = document.createElement("li");
    idle.className = "muted";
    idle.textContent = "空闲中";
    list.appendChild(idle);
    return;
  }
  for (const execution of executions) {
    const item = document.createElement("li");
    item.textContent = formatExecution(execution);
    list.appendChild(item);
  }
}

export function labelsFromText(text) {
  return Object.fromEntries(text.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const separator = line.indexOf("=");
    if (separator < 1) throw new Error(`标签必须使用 key=value 格式：${line}`);
    return [line.slice(0, separator).trim(), line.slice(separator + 1).trim()];
  }));
}

export function numberField(data, key) {
  const raw = data.get(key);
  return raw === null || raw === "" ? NUMBER_DEFAULTS[key] : Number(raw);
}

async function loadStatus() {
  try { renderStatus(await api("/api/status")); } catch (error) { setText("status-detail", error.message); }
}

async function loadLogs() {
  try {
    const payload = await api("/api/logs?limit=200");
    document.querySelector("#logs").textContent = payload.lines.join("\n") || "暂无日志";
  } catch (error) { document.querySelector("#logs").textContent = error.message; }
}

async function loadConfig() {
  try { fillForm(await api("/api/config")); } catch (error) { errorBox.textContent = `加载配置失败：${error.message}`; }
}

if (hasDom) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    setText("save-state", "正在应用…");
    try {
      const data = new FormData(form);
      const payload = {
        host_url: data.get("host_url"), worker_id: data.get("worker_id"), name: data.get("name"),
        max_concurrency: numberField(data, "max_concurrency"), runtimes: data.getAll("runtimes"),
        labels: labelsFromText(data.get("labels")), poll_interval_seconds: numberField(data, "poll_interval_seconds"),
        heartbeat_interval_seconds: numberField(data, "heartbeat_interval_seconds"),
        shutdown_grace_seconds: numberField(data, "shutdown_grace_seconds"),
      };
      const result = await api("/api/config", { method: "PUT", body: JSON.stringify(payload) });
      renderStatus(result.status);
      setText("save-state", "已保存并生效");
      await loadLogs();
    } catch (error) { errorBox.textContent = error.message; setText("save-state", "保存失败"); }
  });

  document.querySelector("#restart").addEventListener("click", async () => {
    try { renderStatus(await api("/api/restart", { method: "POST" })); await loadLogs(); } catch (error) { errorBox.textContent = error.message; }
  });
  document.querySelector("#refresh-logs").addEventListener("click", loadLogs);

  if (TOKEN_MISSING) {
    setText("status-title", "控制令牌未注入");
    setText("status-detail", "请通过 Worker Service（默认 http://127.0.0.1:8787/）访问本页面，静态打开 index.html 无法鉴权");
    errorBox.textContent = "控制令牌未注入，页面不可用";
  } else {
    Promise.all([loadConfig(), loadStatus(), loadLogs()]);
    setInterval(loadStatus, 5000);
  }
}
