// labelsFromText / numberField / NUMBER_DEFAULTS / formatElapsed / executionLabel /
// formatTokens / latestMetric / tokensLastHour / bucketLabel / fillWindowBuckets /
// chartSeriesData / hasChartData 是纯函数，
// 由 app.test.mjs（node:test）直接 import；
// 因此本文件以 ES module 加载（见 index.html 的 script type="module"），DOM 访问做存在性守卫。
const hasDom = typeof document !== "undefined";
const form = hasDom ? document.querySelector("#config-form") : null;
const errorBox = hasDom ? document.querySelector("#form-error") : null;
const injectedToken = typeof window !== "undefined" ? window.__WORKER_CONTROL_TOKEN__ : undefined;
let CONTROL_TOKEN = !injectedToken || injectedToken === "__WORKER_CONTROL_TOKEN__" ? undefined : injectedToken;
// 非回环绑定时服务端不再内嵌 token：先读 sessionStorage，否则提示手动输入并记住本次会话。
if (!CONTROL_TOKEN && hasDom) {
  CONTROL_TOKEN = window.sessionStorage.getItem("worker-control-token") || undefined;
  if (!CONTROL_TOKEN) {
    const entered = window.prompt("请输入 Worker 控制令牌（见 state dir 下的 control_token 文件）");
    if (entered) {
      CONTROL_TOKEN = entered;
      window.sessionStorage.setItem("worker-control-token", entered);
    }
  }
}
const TOKEN_MISSING = !CONTROL_TOKEN;
let latestMaxConcurrency = 0;
// 与 worker/config_store.py 的 _DEFAULTS 对齐：数字字段留空时回退到后端默认值。
export const NUMBER_DEFAULTS = { max_concurrency: 1, upload_max_concurrency: 4, poll_interval_seconds: 2, heartbeat_interval_seconds: 15, shutdown_grace_seconds: 25 };

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
  const element = document.querySelector(`#${id}`);
  if (element) element.textContent = value ?? "—";
}

function setNotice(message) {
  setText("global-notice", message);
}

function formatClock(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function friendlyConnectionError(value) {
  const message = String(value || "");
  if (/401|unauthorized|not authenticated/i.test(message)) return "Host 鉴权失败，请检查 Worker 注册状态";
  return message || "Host 暂时不可达";
}

function makeIcon(name, className = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `/assets/icons.svg#${name}`);
  svg.appendChild(use);
  return svg;
}

function renderStatus(status) {
  const healthy = Boolean(status.connected && status.worker_running);
  const warning = Boolean(status.worker_running && !status.connected);
  const healthIcon = document.querySelector("#health-icon");
  const title = document.querySelector("#status-title");
  healthIcon.className = `health-icon ${healthy ? "" : warning ? "warning" : "offline"}`.trim();
  title.className = healthy ? "" : warning ? "warning-text" : "error-text";
  setText("status-title", healthy ? (status.claim_enabled ? "运行正常" : "Worker 已暂停领取") : warning ? "Worker 等待 Host" : "Worker 尚未就绪");
  setText(
    "status-detail",
    status.failed
      || status.bootstrap_error
      || (status.connection_error ? friendlyConnectionError(status.connection_error) : "")
      || (healthy
        ? status.claim_enabled
          ? "正在领取 · Worker 运行健康，正在处理任务"
          : "现有任务继续执行，不再领取新任务"
        : status.configured ? "本地配置已加载，等待连接恢复" : "请先完成配置"),
  );
  const processState = status.worker_running
    ? `运行中 · PID ${status.pid}`
    : status.failed
      ? "已失败"
      : status.next_restart_delay
        ? `等待自动重启（第 ${status.restart_count} 次）`
        : "未运行";
  setText("process-state", processState);
  setText("claim-state", status.claim_enabled ? "正在领取" : "已暂停");
  document.querySelector("#claim-state").className = status.claim_enabled ? "mint-text" : "amber-text";
  const runningCount = status.running_executions_count ?? 0;
  const capacity = status.max_concurrency ?? 0;
  latestMaxConcurrency = capacity;
  setText("capacity-state", `${runningCount} / ${capacity || "—"}`);
  document.querySelector("#capacity-meter").style.width = `${capacity ? Math.min(100, (runningCount / capacity) * 100) : 0}%`;
  const uploadActive = status.upload_active_count ?? 0;
  const uploadQueued = status.upload_queued_count ?? 0;
  const uploadCapacity = status.upload_max_concurrency ?? 4;
  setText("upload-state", `${uploadActive} / ${uploadCapacity || "—"}`);
  setText("upload-queued", uploadQueued > 0 ? `${uploadQueued} 个排队` : "无排队");
  document.querySelector("#upload-meter").style.width = `${uploadCapacity ? Math.min(100, (uploadActive / uploadCapacity) * 100) : 0}%`;
  const toggle = document.querySelector("#toggle-claiming");
  toggle.dataset.enabled = String(Boolean(status.claim_enabled));
  toggle.querySelector("span").textContent = status.claim_enabled ? "暂停领取" : "开始领取";
  toggle.classList.toggle("paused", !status.claim_enabled);
  setText("host-state", status.host_reachable ? "可达" : "不可达");
  document.querySelector("#host-state").className = status.host_reachable ? "mint-text" : "red-text";
  setText(
    "registered-state",
    status.registered
      ? `${status.host_worker?.worker_id || "本机 Worker"} · ${status.host_worker?.name || "已登记"}`
      : "未登记",
  );
  const scope = status.host_worker?.allowed_workspaces;
  setText("workspace-scope", scope ? (scope.length ? scope.join(", ") : "全部") : "—");
  setText("last-seen", formatClock(status.host_worker?.last_seen_at));
  setText("worker-name", status.host_worker?.name || status.host_worker?.worker_id || "本机 Worker");
  setText("diagnostic-host", status.host_reachable ? "连接正常" : friendlyConnectionError(status.connection_error));
  const topDot = document.querySelector("#worker-online-dot");
  topDot.className = `online-dot ${healthy ? "" : warning ? "warning" : "offline"}`.trim();
  topDot.title = healthy ? "在线" : warning ? "等待 Host" : "离线";
  document.querySelectorAll(".diagnostic-status-grid .diagnostic-dot").forEach((dot, index) => {
    const ok = index === 0 ? status.worker_running : index === 1 ? status.host_reachable : index === 2 ? status.registered : Boolean(scope);
    dot.className = `diagnostic-dot ${ok ? "" : index === 3 && !scope ? "warning" : "offline"}`.trim();
  });
  const source = document.querySelector("#source-warning");
  source.classList.toggle("ok-source", status.host_reachable);
  setText("source-warning-title", status.host_reachable ? "Host 连接正常" : "Host 状态不可用");
  setText(
    "source-warning-detail",
    status.host_reachable
      ? `Host 最后同步 ${formatClock(status.host_worker?.last_seen_at)}`
      : friendlyConnectionError(status.connection_error),
  );
  renderExecutions(status.current_executions, capacity);
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

export function groupExecutions(executions = []) {
  const groups = new Map();
  for (const execution of executions || []) {
    const name = execution.node_key || execution.agent_id || "其他执行";
    if (!groups.has(name)) groups.set(name, { name, phase: execution.phase || "claimed", executions: [] });
    const group = groups.get(name);
    group.executions.push(execution);
    if (phaseProgress(execution.phase) > phaseProgress(group.phase)) group.phase = execution.phase;
  }
  return [...groups.values()].sort((left, right) => right.executions.length - left.executions.length || left.name.localeCompare(right.name));
}

export function phaseProgress(phase) {
  return { claimed: 16, downloading: 34, running: 68, queued_upload: 80, uploading: 88 }[phase] ?? 12;
}

function phaseLabel(phase) {
  return { claimed: "已领取", downloading: "下载中", running: "运行中", queued_upload: "排队上传", uploading: "上传中" }[phase] || phase || "未知";
}

function phaseBadgeClass(phase) {
  if (phase === "uploading" || phase === "queued_upload") return "status-badge uploading";
  if (phase === "running") return "status-badge";
  if (phase === "downloading") return "status-badge info";
  return "status-badge warn";
}

function shortenId(value) {
  const text = String(value || "unknown");
  return text.length > 18 ? `${text.slice(0, 8)}…${text.slice(-4)}` : text;
}

function filterExecutions(query) {
  const needle = String(query || "").trim().toLowerCase();
  document.querySelectorAll(".execution-group").forEach((group) => {
    group.hidden = Boolean(needle && !group.dataset.search.includes(needle));
  });
}

function renderExecutions(executions, maxConcurrency = latestMaxConcurrency) {
  const list = document.querySelector("#executions");
  const groups = groupExecutions(executions);
  list.textContent = "";
  setText("execution-count", `${executions?.length || 0} 个执行`);
  if (groups.length === 0) {
    const idle = document.createElement("div");
    idle.className = "empty-state";
    idle.append(makeIcon("check", "icon empty-icon"));
    const idleCopy = document.createElement("span");
    idleCopy.innerHTML = "<strong>当前没有执行中的 Agent</strong><small>Worker 空闲中，等待 Host 分配新任务</small>";
    idle.appendChild(idleCopy);
    list.appendChild(idle);
    return;
  }
  groups.forEach((group, groupIndex) => {
    const section = document.createElement("section");
    section.className = "execution-group";
    section.dataset.search = `${group.name} ${group.executions.map(executionLabel).join(" ")}`.toLowerCase();
    const button = document.createElement("button");
    button.className = "group-row";
    button.type = "button";
    const initiallyOpen = groupIndex === 0;
    button.setAttribute("aria-expanded", String(initiallyOpen));

    const name = document.createElement("span");
    name.className = "group-name";
    name.append(makeIcon("chevron-down"));
    const mark = document.createElement("span");
    mark.className = `group-mark ${group.phase === "uploading" ? "uploading" : ""}`.trim();
    mark.append(makeIcon(group.phase === "uploading" ? "upload" : "cube"));
    const title = document.createElement("strong");
    title.textContent = group.name;
    const count = document.createElement("span");
    count.className = "count-badge";
    count.textContent = String(group.executions.length);
    name.append(mark, title, count);

    const phase = document.createElement("span");
    phase.className = phaseBadgeClass(group.phase);
    phase.textContent = phaseLabel(group.phase);

    const elapsed = document.createElement("span");
    elapsed.textContent = "—";
    const progress = document.createElement("span");
    progress.className = "group-progress";
    const progressBar = document.createElement("span");
    progressBar.className = "progress-bar";
    const progressFill = document.createElement("i");
    progressFill.style.width = `${phaseProgress(group.phase)}%`;
    progressBar.append(progressFill);
    progress.append(progressBar, `${group.executions.length} 个执行`);
    const capacity = document.createElement("span");
    capacity.className = "group-capacity";
    capacity.title = "当前分组占用 / 本机最大并发";
    capacity.textContent = `${group.executions.length} / ${maxConcurrency || "—"}`;
    button.append(name, phase, elapsed, progress, capacity);

    const children = document.createElement("div");
    children.className = "execution-children";
    children.hidden = !initiallyOpen;
    group.executions.forEach((execution) => {
      const row = document.createElement("div");
      row.className = "execution-row";
      row.setAttribute("role", "row");
      const id = document.createElement("span");
      id.className = "execution-id";
      id.textContent = shortenId(execution.execution_id || execution.job_id || executionLabel(execution));
      const rowPhase = document.createElement("span");
      rowPhase.className = phaseBadgeClass(execution.phase);
      rowPhase.textContent = phaseLabel(execution.phase);
      const rowElapsed = document.createElement("span");
      rowElapsed.textContent = formatElapsed(execution.started_at);
      const rowProgress = document.createElement("span");
      rowProgress.className = "group-progress";
      const rowBar = document.createElement("span");
      rowBar.className = "progress-bar";
      const rowFill = document.createElement("i");
      rowFill.style.width = `${phaseProgress(execution.phase)}%`;
      rowBar.append(rowFill);
      rowProgress.append(rowBar, phaseLabel(execution.phase));
      const rowCapacity = document.createElement("span");
      rowCapacity.title = "当前执行占用 / 本机最大并发";
      rowCapacity.textContent = `1 / ${maxConcurrency || "—"}`;
      row.append(id, rowPhase, rowElapsed, rowProgress, rowCapacity);
      children.appendChild(row);
    });
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!open));
      children.hidden = open;
    });
    section.append(button, children);
    list.appendChild(section);
  });
  filterExecutions(document.querySelector("#execution-search")?.value || "");
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
  // 30d 的 4 小时桶需要「日期 + 小时」才能区分同一天内的多个桶
  if (granularity === "30d") return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:00`;
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const WINDOW_STEP_MS = { "6h": 60_000, "24h": 300_000, "30d": 14_400_000 };
const WINDOW_BUCKET_COUNT = { "6h": 360, "24h": 288, "30d": 180 };

// 把稀疏 bucket 补齐成固定长度时间网格（UTC 对齐，与 Host epoch-floor 汇总一致），
// 横轴窗口固定，不随本机实际有数据的时间段变化。
// 6h（1 分钟桶）结束于上一个已完成分钟；24h（5 分钟桶）/ 30d（4 小时桶）结束于
// 当前进行中的桶（聚合自已有分钟行）。
// 窗口内、最后一个真实数据点之前的缺失桶填零（确实没数据）；
// 右端 Host 尚未聚合写入的尾部缺失桶填 null（数据未出），由图表渲染成缺口而不是掉到 0。
export function fillWindowBuckets(buckets, granularity, now = Date.now()) {
  const step = WINDOW_STEP_MS[granularity];
  const aligned = Math.floor(now / step) * step;
  const end = granularity === "6h" ? aligned - step : aligned;
  const start = end - (WINDOW_BUCKET_COUNT[granularity] - 1) * step;
  const byStart = new Map((buckets ?? []).map((bucket) => [Date.parse(bucket.bucket_start), bucket]));
  const lastDataTs = Math.max(-Infinity, ...(buckets ?? []).map((bucket) => Date.parse(bucket.bucket_start)));
  const empty = (t, pending) => ({
    bucket_start: new Date(t).toISOString(),
    online_workers: pending ? null : 0,
    active_executions: pending ? null : 0,
    input_tokens: pending ? null : 0,
    output_tokens: pending ? null : 0,
    cache_read_tokens: pending ? null : 0,
    total_tokens: pending ? null : 0,
  });
  const filled = [];
  for (let t = start; t <= end; t += step) filled.push(byStart.get(t) ?? empty(t, t > lastDataTs));
  return filled;
}

// uPlot 数据：x 为 unix 秒；y 中 null（数据未出）由 uPlot 原生渲染为缺口。
export function chartSeriesData(buckets, keys) {
  const x = buckets.map((bucket) => Date.parse(bucket.bucket_start) / 1000);
  return [x, ...keys.map((key) => buckets.map((bucket) => bucket[key]))];
}

export function hasChartData(buckets, keys) {
  return buckets.some((bucket) => keys.some((key) => bucket[key] != null));
}

function hexToRgba(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// 每个图表容器一个 uPlot 实例；series/粒度不变时仅 setData 增量刷新。
const metricsCharts = new Map();

function destroyChart(el) {
  const chart = metricsCharts.get(el);
  if (chart) {
    chart.destroy();
    metricsCharts.delete(el);
  }
}

// 用 uPlot 渲染阶梯折线（step-after：值保持整个采样桶），legend 实时跟随光标数值。
// series: [{ key, name, color, area? }]；全部无数据时回退「暂无监控数据」占位。
function renderUplotChart(el, buckets, series, { height, formatValue, granularity }) {
  if (!el || typeof window === "undefined" || !window.uPlot) return;
  const keys = series.map((item) => item.key);
  if (!hasChartData(buckets, keys)) {
    destroyChart(el);
    el.innerHTML = '<p class="chart-empty">暂无监控数据</p>';
    return;
  }
  const data = chartSeriesData(buckets, keys);
  const reuseKey = `${granularity}:${keys.join(",")}`;
  const existing = metricsCharts.get(el);
  if (existing && existing._agentLegionKey === reuseKey) {
    existing.setData(data);
    return;
  }
  destroyChart(el);
  el.innerHTML = "";
  const chart = new window.uPlot(
    {
      width: el.clientWidth || 520,
      height,
      // 右/上留 8px 内边距，避免末端的点和峰值的线贴画布边被裁（CSS px）
      padding: [8, 8, 0, 0],
      cursor: { drag: { x: false, y: false } },
      scales: {
        x: { time: false },
        // y 从 0 起，顶部留 10% 余量避免峰值贴边
        y: { range: (_u, _min, max) => [0, max == null || max <= 0 ? 1 : max * 1.1] },
      },
      axes: [
        {
          stroke: "#668078",
          font: "10px ui-monospace, monospace",
          grid: { show: false },
          ticks: { show: false },
          values: (_u, splits) => splits.map((ts) => bucketLabel(new Date(ts * 1000).toISOString(), granularity)),
        },
        {
          stroke: "#668078",
          font: "10px ui-monospace, monospace",
          grid: { stroke: "#1d392f", width: 1 },
          ticks: { show: false },
          values: (_u, splits) => splits.map((v) => formatValue(v)),
        },
      ],
      series: [
        {},
        ...series.map((item) => ({
          label: item.name,
          stroke: item.color,
          width: 1.6,
          fill: item.area ? hexToRgba(item.color, 0.18) : undefined,
          paths: window.uPlot.paths.stepped({ align: 1 }),
          points: { show: false },
          value: (_u, v) => (v == null ? "—" : formatValue(v)),
        })),
      ],
    },
    data,
    el,
  );
  chart._agentLegionKey = reuseKey;
  metricsCharts.set(el, chart);
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
let metricsGranularity = "6h";

// 纯函数：组装本地缓存指标查询；Worker 身份由执行子进程的签发 token 决定。
// 窗口（6h/24h/30d）由 granularity 唯一决定，不再带 hours/days 参数。
export function metricsParams(granularity) {
  return { granularity, worker_id: "self" };
}

const CAPACITY_SERIES = [{ key: "active_executions", name: "活跃执行", color: "#66e8ad", area: true }];
const TOKEN_SERIES = [
  { key: "input_tokens", name: "输入", color: "#5b9cf5" },
  { key: "output_tokens", name: "输出", color: "#a78bfa" },
  { key: "cache_read_tokens", name: "缓存读", color: "#62e4ad" },
];

function renderMetrics(payload) {
  const granularity = payload.granularity ?? metricsGranularity;
  const buckets = fillWindowBuckets(payload.buckets ?? [], granularity);
  const formatCount = (v) => String(Math.round(v));
  renderUplotChart(document.querySelector("#chart-fleet"), buckets, CAPACITY_SERIES, {
    height: 260,
    formatValue: formatCount,
    granularity,
  });
  renderUplotChart(document.querySelector("#chart-tokens"), buckets, TOKEN_SERIES, {
    height: 260,
    formatValue: formatTokens,
    granularity,
  });
  if (granularity === "6h") {
    renderUplotChart(document.querySelector("#chart-capacity"), buckets, CAPACITY_SERIES, {
      height: 180,
      formatValue: formatCount,
      granularity,
    });
  }
}

async function loadMetrics() {
  const metricsError = document.querySelector("#metrics-error");
  const overviewError = document.querySelector("#overview-metrics-error");
  try {
    const params = new URLSearchParams(metricsParams(metricsGranularity));
    const payload = await api(`/api/metrics/overview?${params}`);
    metricsError.textContent = "";
    overviewError.textContent = "";
    renderMetrics(payload);
  } catch (error) {
    const message = friendlyConnectionError(error.message);
    metricsError.textContent = `监控数据不可用：${message}`;
    overviewError.textContent = `监控数据不可用：${message}`;
  }
}

if (hasDom) {
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => {
      const page = button.dataset.page;
      document.querySelectorAll(".nav-button").forEach((item) => {
        const active = item === button;
        item.classList.toggle("active", active);
        if (active) item.setAttribute("aria-current", "page");
        else item.removeAttribute("aria-current");
      });
      document.querySelectorAll(".page-view").forEach((view) => {
        const active = view.dataset.view === page;
        view.hidden = !active;
        view.classList.toggle("active", active);
      });
    });
  });

  document.querySelector("#execution-search").addEventListener("input", (event) => {
    filterExecutions(event.currentTarget.value);
  });

  const moreActions = document.querySelector("#more-actions");
  const actionMenu = document.querySelector("#action-menu");
  moreActions.addEventListener("click", () => {
    const open = moreActions.getAttribute("aria-expanded") === "true";
    moreActions.setAttribute("aria-expanded", String(!open));
    actionMenu.hidden = open;
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".menu-wrap")) {
      moreActions.setAttribute("aria-expanded", "false");
      actionMenu.hidden = true;
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    setText("save-state", "正在应用…");
    try {
      const data = new FormData(form);
      const payload = {
        host_url: data.get("host_url"), worker_id: data.get("worker_id"), name: data.get("name"),
        max_concurrency: numberField(data, "max_concurrency"), upload_max_concurrency: numberField(data, "upload_max_concurrency"),
        runtimes: data.getAll("runtimes"), capabilities: linesFromText(data.get("capabilities")),
        models: modelsFromText(data.get("models")), labels: labelsFromText(data.get("labels")),
        poll_interval_seconds: numberField(data, "poll_interval_seconds"),
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
      setNotice(result.restarted ? "配置已保存并重启" : "配置已保存");
      await loadLogs();
      loadMetrics();
    } catch (error) { errorBox.textContent = error.message; setText("save-state", "保存失败"); }
  });

  document.querySelector("#restart").addEventListener("click", async () => {
    actionMenu.hidden = true;
    moreActions.setAttribute("aria-expanded", "false");
    if (!window.confirm("重启会中断当前正在执行的任务，确认继续吗？")) return;
    try {
      setNotice("正在重启 Worker…");
      renderStatus(await api("/api/restart", { method: "POST" }));
      setNotice("Worker 已重启");
      await loadLogs();
    } catch (error) {
      errorBox.textContent = error.message;
      setNotice("重启失败");
    }
  });
  document.querySelector("#toggle-claiming").addEventListener("click", async (event) => {
    errorBox.textContent = "";
    const enabled = event.currentTarget.dataset.enabled !== "true";
    try {
      const result = await api("/api/config", { method: "PUT", body: JSON.stringify({ claim_enabled: enabled }) });
      renderStatus(result.status);
      setText("save-state", enabled ? "已开始领取新任务" : "已暂停领取新任务");
      setNotice(enabled ? "已开始领取新任务" : "已暂停领取新任务");
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
    setText("status-detail", "请刷新页面并按提示输入控制令牌；本机默认走 http://127.0.0.1:8787/ 访问时自动注入");
    errorBox.textContent = "控制令牌未注入，页面不可用";
  } else {
    Promise.all([loadConfig(), loadStatus(), loadLogs(), loadMetrics()]);
    setInterval(loadStatus, 5000);
    setInterval(loadMetrics, 30000);
  }
}
