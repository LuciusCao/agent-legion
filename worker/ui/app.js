// labelsFromText / numberField / NUMBER_DEFAULTS / formatElapsed / executionLabel /
// formatTokens / latestMetric / tokensLastHour / bucketLabel / buildLineChart 是纯函数，
// 由 app.test.mjs（node:test）直接 import；
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
  setText("claim-state", status.claim_enabled ? "正在领取" : "已暂停");
  setText("capacity-state", `${status.current_executions?.length || 0} / ${status.max_concurrency ?? "—"}`);
  const toggle = document.querySelector("#toggle-claiming");
  toggle.dataset.enabled = String(Boolean(status.claim_enabled));
  toggle.textContent = status.claim_enabled ? "暂停领取" : "开始领取";
  toggle.classList.toggle("paused", !status.claim_enabled);
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
    } else if (key === "capabilities") {
      form.elements.capabilities.value = value.join("\n");
    } else if (key === "models") {
      form.elements.models.value = value.map((item) => `${item.provider}/${item.model}`).join("\n");
    } else if (key === "labels") {
      form.elements.labels.value = Object.entries(value).map(([label, item]) => `${label}=${item}`).join("\n");
    } else if (form.elements[key]) {
      form.elements[key].value = value;
    }
  }
  setText("register-token-state", config.register_token_configured ? "已配置；留空保持不变" : "尚未配置");
}

export function formatElapsed(started_at, now = Date.now()) {
  const started = Date.parse(started_at);
  const elapsed = Number.isNaN(started) ? 0 : Math.max(0, Math.floor((now - started) / 1000));
  const minutes = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const seconds = String(elapsed % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

export function executionLabel(execution) {
  return [execution.agent_id, execution.node_key].filter(Boolean).join(" · ") || execution.execution_id || "unknown";
}

function renderExecutions(executions) {
  const list = document.querySelector("#executions");
  list.textContent = "";
  if (!executions || executions.length === 0) {
    const idle = document.createElement("li");
    idle.className = "muted executions-empty";
    idle.textContent = "空闲中";
    list.appendChild(idle);
    return;
  }
  for (const execution of executions) {
    const item = document.createElement("li");
    item.className = "execution-card";
    const head = document.createElement("div");
    head.className = "execution-head";
    const title = document.createElement("span");
    title.className = "execution-title";
    title.textContent = executionLabel(execution);
    const badge = document.createElement("span");
    const phase = execution.phase || "?";
    badge.className = `phase-badge phase-${phase}`;
    badge.textContent = phase;
    head.append(title, badge);
    const meta = document.createElement("dl");
    meta.className = "execution-meta";
    for (const [label, value] of [
      ["Job", execution.job_id || "—"],
      ["已运行", formatElapsed(execution.started_at)],
    ]) {
      const cell = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value;
      cell.append(term, detail);
      meta.appendChild(cell);
    }
    item.append(head, meta);
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

export function linesFromText(text) {
  return [...new Set(text.split("\n").map((line) => line.trim()).filter(Boolean))];
}

export function modelsFromText(text) {
  return linesFromText(text).map((line) => {
    const separator = line.indexOf("/");
    if (separator < 1 || separator === line.length - 1) throw new Error(`模型必须使用 provider/model 格式：${line}`);
    return { provider: line.slice(0, separator).trim(), model: line.slice(separator + 1).trim() };
  });
}

export function numberField(data, key) {
  const raw = data.get(key);
  return raw === null || raw === "" ? NUMBER_DEFAULTS[key] : Number(raw);
}

// ---- Host 监控面板：纯数据/渲染函数（无 DOM），供 app.test.mjs 直接测试 ----

export function formatTokens(value) {
  if (value == null || Number.isNaN(value)) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1_000)}k`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}

export function latestMetric(payload, key) {
  const buckets = payload?.buckets ?? [];
  const last = buckets[buckets.length - 1];
  return last && last[key] != null ? last[key] : null;
}

export function tokensLastHour(buckets, now = Date.now()) {
  const all = buckets ?? [];
  const cutoff = now - 3_600_000;
  const recent = all.filter((bucket) => Date.parse(bucket.bucket_start) >= cutoff);
  const source = recent.length ? recent : all.slice(-1);
  return source.reduce((sum, bucket) => sum + (bucket.total_tokens || 0), 0);
}

export function bucketLabel(bucketStart, granularity) {
  const date = new Date(bucketStart);
  if (Number.isNaN(date.getTime())) return String(bucketStart);
  const pad = (n) => String(n).padStart(2, "0");
  if (granularity === "day") return `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const WINDOW_STEP_MS = { minute: 60_000, hour: 3_600_000, day: 86_400_000 };
const WINDOW_BUCKET_COUNT = { minute: 360, hour: 24, day: 7 };

// 把稀疏 bucket 补齐成固定长度时间网格（UTC 对齐，与 Host date_trunc 汇总一致），
// 缺失的桶填零：横轴窗口固定，不随本机实际有数据的时间段变化。
export function fillWindowBuckets(buckets, granularity, now = Date.now()) {
  const step = WINDOW_STEP_MS[granularity];
  const aligned = Math.floor(now / step) * step;
  const end = granularity === "minute" ? aligned - step : aligned;
  const start = end - (WINDOW_BUCKET_COUNT[granularity] - 1) * step;
  const byStart = new Map((buckets ?? []).map((bucket) => [Date.parse(bucket.bucket_start), bucket]));
  const zero = (t) => ({
    bucket_start: new Date(t).toISOString(),
    online_workers: 0,
    active_executions: 0,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    total_tokens: 0,
  });
  const filled = [];
  for (let t = start; t <= end; t += step) filled.push(byStart.get(t) ?? zero(t));
  return filled;
}

const escapeXml = (text) => String(text).replace(/[<>&"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" })[c]);

// 手绘 SVG 折线/面积图（viewBox 自适应宽度）；hover 数值用 <title> 原生提示，无 JS 事件。
// seriesList: [{ name, color, points: [{ label, value }], area? }]
export function buildLineChart(seriesList, { width = 520, height = 180, formatValue = (v) => String(v) } = {}) {
  const series = seriesList.filter((item) => item.points.length > 0);
  const count = Math.max(0, ...series.map((item) => item.points.length));
  if (count === 0) return "";
  const pad = { left: 36, right: 10, top: 18, bottom: 20 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const maxValue = Math.max(1, ...series.flatMap((item) => item.points.map((point) => point.value ?? 0)));
  const x = (index) => pad.left + (count === 1 ? innerW / 2 : (index / (count - 1)) * innerW);
  const y = (value) => pad.top + innerH - (Math.max(0, value ?? 0) / maxValue) * innerH;
  const baseline = y(0);
  const parts = [];
  for (const tick of [0, maxValue / 2, maxValue]) {
    parts.push(`<line class="grid-line" x1="${pad.left}" y1="${y(tick)}" x2="${width - pad.right}" y2="${y(tick)}"/>`);
    parts.push(`<text class="axis" x="${pad.left - 4}" y="${y(tick) + 3}" text-anchor="end">${escapeXml(formatValue(tick))}</text>`);
  }
  const labels = series[0].points;
  for (const index of [...new Set([0, Math.floor((count - 1) / 2), count - 1])]) {
    parts.push(`<text class="axis" x="${x(index)}" y="${height - 6}" text-anchor="middle">${escapeXml(labels[index].label)}</text>`);
  }
  series.forEach((item, i) => {
    parts.push(`<rect x="${pad.left + i * 110}" y="4" width="8" height="8" rx="2" fill="${item.color}"/>`);
    parts.push(`<text class="axis" x="${pad.left + i * 110 + 12}" y="11">${escapeXml(item.name)}</text>`);
  });
  for (const item of series) {
    const coords = item.points.map((point, i) => `${x(i).toFixed(1)},${y(point.value).toFixed(1)}`);
    if (item.area) {
      parts.push(`<path d="M ${x(0).toFixed(1)},${baseline} L ${coords.join(" L ")} L ${x(item.points.length - 1).toFixed(1)},${baseline} Z" fill="${item.color}" opacity="0.18"/>`);
    }
    parts.push(`<polyline points="${coords.join(" ")}" fill="none" stroke="${item.color}" stroke-width="1.6" stroke-linejoin="round"/>`);
  }
  const zoneWidth = innerW / count;
  for (let i = 0; i < count; i++) {
    const values = series.map((item) => `${item.name}: ${formatValue(item.points[i]?.value ?? 0)}`).join("\n");
    parts.push(`<rect class="hover-zone" x="${(x(i) - zoneWidth / 2).toFixed(1)}" y="${pad.top}" width="${zoneWidth.toFixed(1)}" height="${innerH}"><title>${escapeXml(`${labels[i].label}\n${values}`)}</title></rect>`);
  }
  return `<svg viewBox="0 0 ${width} ${height}" role="img">${parts.join("")}</svg>`;
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
  try {
    const config = await api("/api/config");
    fillForm(config);
  } catch (error) { errorBox.textContent = `加载配置失败：${error.message}`; }
}

// ---- Host 监控面板：加载与 DOM 渲染（只看本机 Worker 切片） ----
let metricsGranularity = "minute";
const GRANULARITY_QUERY = { minute: { hours: 6 }, hour: { hours: 24 }, day: { days: 7 } };

// 纯函数：组装 /api/metrics/overview 的查询参数；worker_id=self 由服务端解析成本机 id。
export function metricsParams(granularity) {
  return { granularity, ...GRANULARITY_QUERY[granularity], worker_id: "self" };
}

function renderMetrics(payload) {
  const granularity = payload.granularity ?? metricsGranularity;
  const buckets = fillWindowBuckets(payload.buckets ?? [], granularity);
  setText("metric-executions", latestMetric({ buckets }, "active_executions"));
  setText("metric-tokens", formatTokens(tokensLastHour(buckets)));
  const points = (key) => buckets.map((bucket) => ({ label: bucketLabel(bucket.bucket_start, granularity), value: bucket[key] }));
  const fleet = buildLineChart(
    [{ name: "活跃执行", color: "#f0a83c", points: points("active_executions"), area: true }],
    { formatValue: (v) => String(Math.round(v)) },
  );
  const tokens = buildLineChart(
    [
      { name: "输入", color: "#5b9cf5", points: points("input_tokens") },
      { name: "输出", color: "#a78bfa", points: points("output_tokens") },
      { name: "缓存读", color: "#62e4ad", points: points("cache_read_tokens") },
    ],
    { formatValue: formatTokens },
  );
  const empty = '<p class="chart-empty">暂无监控数据</p>';
  document.querySelector("#chart-fleet").innerHTML = fleet || empty;
  document.querySelector("#chart-tokens").innerHTML = tokens || empty;
}

async function loadMetrics() {
  const metricsError = document.querySelector("#metrics-error");
  try {
    const params = new URLSearchParams(metricsParams(metricsGranularity));
    const payload = await api(`/api/metrics/overview?${params}`);
    metricsError.textContent = "";
    renderMetrics(payload);
  } catch (error) {
    metricsError.textContent = `监控数据不可用：${error.message}`;
  }
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
        capabilities: linesFromText(data.get("capabilities")), models: modelsFromText(data.get("models")),
        labels: labelsFromText(data.get("labels")), poll_interval_seconds: numberField(data, "poll_interval_seconds"),
        heartbeat_interval_seconds: numberField(data, "heartbeat_interval_seconds"),
        shutdown_grace_seconds: numberField(data, "shutdown_grace_seconds"),
      };
      const registerToken = data.get("register_token").trim();
      if (registerToken) payload.register_token = registerToken;
      const result = await api("/api/config", { method: "PUT", body: JSON.stringify(payload) });
      renderStatus(result.status);
      form.elements.register_token.value = "";
      fillForm(result.config);
      setText("save-state", result.restarted ? "已保存并重启生效" : "已热更新");
      await loadLogs();
      loadMetrics();
    } catch (error) { errorBox.textContent = error.message; setText("save-state", "保存失败"); }
  });

  document.querySelector("#restart").addEventListener("click", async () => {
    try { renderStatus(await api("/api/restart", { method: "POST" })); await loadLogs(); } catch (error) { errorBox.textContent = error.message; }
  });
  document.querySelector("#toggle-claiming").addEventListener("click", async (event) => {
    errorBox.textContent = "";
    const enabled = event.currentTarget.dataset.enabled !== "true";
    try {
      const result = await api("/api/config", { method: "PUT", body: JSON.stringify({ claim_enabled: enabled }) });
      renderStatus(result.status);
      setText("save-state", enabled ? "已开始领取新任务" : "已暂停领取新任务");
    } catch (error) { errorBox.textContent = error.message; }
  });
  document.querySelector("#refresh-logs").addEventListener("click", loadLogs);
  document.querySelectorAll(".granularity-switch .chip").forEach((button) => {
    button.addEventListener("click", () => {
      metricsGranularity = button.dataset.granularity;
      document.querySelectorAll(".granularity-switch .chip").forEach((chip) => chip.classList.toggle("active", chip === button));
      loadMetrics();
    });
  });

  if (TOKEN_MISSING) {
    setText("status-title", "控制令牌未注入");
    setText("status-detail", "请通过 Worker Service（默认 http://127.0.0.1:8787/）访问本页面，静态打开 index.html 无法鉴权");
    errorBox.textContent = "控制令牌未注入，页面不可用";
  } else {
    Promise.all([loadConfig(), loadStatus(), loadLogs(), loadMetrics()]);
    setInterval(loadStatus, 5000);
    setInterval(loadMetrics, 30000);
  }
}
