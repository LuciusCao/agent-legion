import "./styles.css";

type VideoItem = {
  id: string;
  title: string;
  source_url: string;
  content_type: "knowledge" | "question";
  external_id: string;
  knowledge_code: string;
  question_id: string;
  status: string;
  current_phase: string;
  error_message: string;
};

type AgentStatus = {
  id: string;
  name: string;
  busy: boolean;
  current_video_id: string | null;
  current_title?: string;
  current_content_type?: "knowledge" | "question" | "";
  current_external_id?: string;
  current_phase?: string;
};

type VideoArtifacts = {
  subtitles: Array<{ index: number; start: number; end: number; text: string }>;
  chapters: Array<{ id: string; start_time: number; end_time: number; title: string }>;
  interactions: Array<Record<string, unknown>>;
  metadata: Record<string, unknown> | null;
};

type ContentType = "knowledge" | "question";
type ViewName = "list" | "detail";
type DetailTab = "nodes" | "subtitles" | "chapters" | "logs" | "metadata";

type AddResult = {
  external_id: string;
  content_type: ContentType;
  status: string;
  message: string;
  video?: VideoItem;
};

type BatchResult = {
  video_id: string;
  status: string;
  phase?: string;
  message: string;
};

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) {
  throw new Error("App root not found");
}

let videos: VideoItem[] = [];
let selectedId = "";
let selectedType: ContentType = "knowledge";
let activeView: ViewName = "list";
let statusFilter = "all";
let searchQuery = "";
let selectMode = false;
let selectedIds = new Set<string>();
let agents: AgentStatus[] = [];
let currentArtifacts: VideoArtifacts = { subtitles: [], chapters: [], interactions: [], metadata: null };
let currentLog = "";
let activeTab: DetailTab = "nodes";
let triggeredNodeIndexes = new Set<number>();
let currentSentence: string[] = [];
let addContentType: ContentType = "knowledge";

app.innerHTML = `
  <main class="app-shell">
    <section id="listView" class="view workbench-view">
      <header class="topbar">
        <div>
          <h1>Video Hive</h1>
          <p id="workbenchSubtitle">资源处理队列</p>
        </div>
        <div class="topbar-actions">
          <button id="refreshBtn" class="icon-button" title="刷新">↻</button>
          <button id="selectModeBtn">多选</button>
          <button id="addOpenBtn" class="primary-button">+</button>
        </div>
      </header>

      <section class="filters-row">
        <div id="typeTabs" class="segmented-control">
          <button data-type="knowledge" class="active">知识点</button>
          <button data-type="question">题目</button>
        </div>
        <input id="searchInput" placeholder="搜索 ID、标题或内部记录" />
        <select id="statusFilter">
          <option value="all">全部状态</option>
          <option value="missing_url">等待 URL</option>
          <option value="queued">排队中</option>
          <option value="running">处理中</option>
          <option value="failed">失败</option>
          <option value="completed">已完成</option>
        </select>
      </section>

      <section id="statsPanel" class="stats-panel"></section>
      <section id="agentPanel" class="agent-panel"></section>
      <section id="batchToolbar" class="batch-toolbar hidden"></section>
      <section id="groupedList" class="grouped-list"></section>
    </section>

    <section id="detailView" class="view detail-view hidden">
      <header class="detail-topbar">
        <button id="backBtn">返回</button>
        <div class="detail-title-block">
          <h1 id="detailTitle">未选择资源</h1>
          <p id="detailSubtitle"></p>
        </div>
        <div class="detail-actions">
          <select id="rerunPhase"></select>
          <button id="rerunBtn">重跑</button>
          <button id="detailPackageBtn">打包</button>
          <button id="deleteBtn" class="danger-button">删除</button>
        </div>
      </header>
      <section class="preview-layout">
        <div class="preview-main">
          <div id="playerWrap" class="player-wrap">
            <div class="empty-state">请选择资源</div>
          </div>
          <div id="chaptersStrip" class="chapters-strip"></div>
        </div>
        <aside id="playInfoPanel" class="play-info-panel"></aside>
      </section>
      <section class="detail-bottom">
        <nav id="detailTabs" class="tabs"></nav>
        <div id="tabPanel" class="tab-panel"></div>
      </section>
    </section>

    <dialog id="addDialog" class="add-dialog">
      <form id="addForm" method="dialog">
        <header class="dialog-header">
          <h2>添加资源</h2>
          <button id="addCloseBtn" type="button" class="icon-button">×</button>
        </header>
        <div class="segmented-control dialog-type">
          <button type="button" data-add-type="knowledge" class="active">知识点</button>
          <button type="button" data-add-type="question">题目</button>
        </div>
        <textarea id="resourceIdsInput" placeholder="一行一个知识点 code 或题目 ID"></textarea>
        <footer class="dialog-footer">
          <button id="addSubmitBtn" type="submit" class="primary-button">加入队列</button>
        </footer>
        <div id="addResults" class="add-results"></div>
      </form>
    </dialog>
  </main>
`;

const byId = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element #${id}`);
  return el as T;
};

const TYPE_LABELS: Record<ContentType, string> = { knowledge: "知识点", question: "题目" };
const STATUS_LABELS: Record<string, string> = {
  missing_url: "等待 URL",
  queued: "排队中",
  running: "处理中",
  failed: "失败",
  completed: "已完成",
};
const PHASE_LABELS: Record<string, string> = {
  waiting_for_url: "等待 URL",
  download: "下载",
  transcribe: "转录",
  subtitle_review: "字幕 review",
  chapter_generate: "章节生成",
  interaction_generate: "互动生成",
  content_review: "内容 review",
  assemble: "组装",
  package: "打包",
};

const KNOWLEDGE_PHASES = [
  "download",
  "transcribe",
  "subtitle_review",
  "chapter_generate",
  "interaction_generate",
  "content_review",
  "assemble",
];
const QUESTION_PHASES = ["download", "transcribe", "subtitle_review", "chapter_generate", "assemble"];

function statusGroup(video: VideoItem): string {
  if (video.status === "missing_url" || video.current_phase === "waiting_for_url") return "missing_url";
  if (video.status === "failed") return "failed";
  if (video.status === "completed") return "completed";
  if (video.status === "running") return "running";
  return "queued";
}

function filteredVideos(): VideoItem[] {
  const query = searchQuery.trim().toLowerCase();
  return videos.filter((video) => {
    if (video.content_type !== selectedType) return false;
    if (statusFilter !== "all" && statusGroup(video) !== statusFilter) return false;
    if (!query) return true;
    return [video.id, video.title, video.external_id].some((value) => value.toLowerCase().includes(query));
  });
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) throw new Error(await response.text());
  return (await response.json()) as T;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return map[char] ?? char;
  });
}

function renderStats(): void {
  const scoped = videos.filter((video) => video.content_type === selectedType);
  const counts = ["missing_url", "queued", "running", "failed", "completed"].map((key) => ({
    key,
    label: STATUS_LABELS[key],
    count: scoped.filter((video) => statusGroup(video) === key).length,
  }));
  byId<HTMLDivElement>("statsPanel").innerHTML = [
    `<button class="stat-card ${statusFilter === "all" ? "active" : ""}" data-status="all"><span>全部</span><strong>${scoped.length}</strong></button>`,
    ...counts.map(
      (item) =>
        `<button class="stat-card ${statusFilter === item.key ? "active" : ""}" data-status="${item.key}"><span>${item.label}</span><strong>${item.count}</strong></button>`,
    ),
  ].join("");
  byId<HTMLDivElement>("statsPanel").querySelectorAll<HTMLButtonElement>("[data-status]").forEach((button) => {
    button.addEventListener("click", () => {
      statusFilter = button.dataset.status ?? "all";
      byId<HTMLSelectElement>("statusFilter").value = statusFilter;
      renderListView();
    });
  });
}

function renderGroupedList(): void {
  const list = byId<HTMLDivElement>("groupedList");
  const items = filteredVideos();
  const order = ["missing_url", "queued", "running", "failed", "completed"];
  list.innerHTML = order
    .map((groupKey) => {
      const groupItems = items.filter((video) => statusGroup(video) === groupKey);
      return `
        <section class="resource-group">
          <header class="group-header">
            <h2>${STATUS_LABELS[groupKey]}</h2>
            <span>${groupItems.length}</span>
          </header>
          <div class="resource-rows">
            ${groupItems.map(renderVideoRow).join("") || `<div class="empty-row">暂无资源</div>`}
          </div>
        </section>
      `;
    })
    .join("");

  list.querySelectorAll<HTMLButtonElement>(".resource-row").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.id ?? "";
      if (selectMode) {
        if (selectedIds.has(id)) selectedIds.delete(id);
        else selectedIds.add(id);
        renderListView();
      } else {
        void selectVideo(id);
      }
    });
  });
}

function renderVideoRow(video: VideoItem): string {
  const checked = selectedIds.has(video.id) ? "checked" : "";
  const agent = agents.find((item) => item.current_video_id === video.id);
  const error = video.error_message ? `<small class="row-error">${escapeHtml(video.error_message)}</small>` : "";
  return `
    <button class="resource-row ${selectedId === video.id ? "active" : ""}" data-id="${video.id}">
      ${selectMode ? `<span class="fake-check ${checked}"></span>` : ""}
      <span class="resource-id">${escapeHtml(video.external_id || video.id)}</span>
      <span class="resource-main">
        <strong>${escapeHtml(video.title)}</strong>
        <small>${PHASE_LABELS[video.current_phase] ?? video.current_phase} · ${STATUS_LABELS[statusGroup(video)]}</small>
        ${error}
      </span>
      ${agent ? `<span class="agent-pill">${escapeHtml(agent.name)}</span>` : ""}
    </button>
  `;
}

function renderListView(): void {
  byId<HTMLDivElement>("listView").classList.toggle("hidden", activeView !== "list");
  byId<HTMLDivElement>("detailView").classList.toggle("hidden", activeView !== "detail");
  byId<HTMLParagraphElement>("workbenchSubtitle").textContent = `${TYPE_LABELS[selectedType]}资源处理队列`;
  byId<HTMLDivElement>("typeTabs")
    .querySelectorAll<HTMLButtonElement>("[data-type]")
    .forEach((button) => button.classList.toggle("active", button.dataset.type === selectedType));
  renderStats();
  renderAgents();
  renderBatchToolbar();
  renderGroupedList();
}

function renderAgents(): void {
  const panel = byId<HTMLDivElement>("agentPanel");
  if (agents.length === 0) {
    panel.innerHTML = `<div class="empty-row">暂无可用 Agent</div>`;
    return;
  }
  const idleCount = agents.filter((agent) => !agent.busy).length;
  panel.innerHTML = `
    <header class="agent-summary">Agent ${idleCount}/${agents.length} 空闲</header>
    <div class="agent-list">
      ${agents
        .map(
          (agent) => `
            <div class="agent-card ${agent.busy ? "busy" : "idle"}">
              <span class="agent-dot"></span>
              <strong>${escapeHtml(agent.name)}</strong>
              <span>${agent.busy ? "处理中" : "空闲"}</span>
              ${
                agent.current_video_id
                  ? `<small>${escapeHtml(agent.current_external_id || agent.current_video_id)} · ${escapeHtml(PHASE_LABELS[agent.current_phase || ""] ?? agent.current_phase ?? "")}</small>`
                  : ""
              }
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderAddDialogType(): void {
  byId<HTMLDialogElement>("addDialog")
    .querySelectorAll<HTMLButtonElement>("[data-add-type]")
    .forEach((button) => button.classList.toggle("active", button.dataset.addType === addContentType));
  byId<HTMLTextAreaElement>("resourceIdsInput").placeholder =
    addContentType === "knowledge" ? "一行一个知识点 code" : "一行一个题目 ID";
}

function renderAddResults(results: AddResult[]): void {
  const panel = byId<HTMLDivElement>("addResults");
  if (results.length === 0) {
    panel.innerHTML = "";
    return;
  }
  panel.innerHTML = `
    <div class="result-summary">成功 ${results.filter((r) => r.status.startsWith("created")).length} 条，失败 ${results.filter((r) => !r.status.startsWith("created") && r.status !== "duplicate").length} 条，重复 ${results.filter((r) => r.status === "duplicate").length} 条</div>
    ${results
      .map(
        (result) => `
          <div class="add-result ${result.status}">
            <strong>${escapeHtml(result.external_id)}</strong>
            <span>${escapeHtml(result.status)}</span>
            <small>${escapeHtml(result.message || result.video?.title || "")}</small>
          </div>
        `,
      )
      .join("")}
  `;
}

function renderBatchToolbar(): void {
  const toolbar = byId<HTMLDivElement>("batchToolbar");
  toolbar.classList.toggle("hidden", !selectMode);
  if (!selectMode) return;
  const phases = selectedType === "question" ? QUESTION_PHASES : KNOWLEDGE_PHASES;
  toolbar.innerHTML = `
    <span>已选 ${selectedIds.size} 项</span>
    <button id="selectVisibleBtn">全选当前结果</button>
    <button id="clearSelectedBtn">清空</button>
    <select id="batchPhase">
      ${phases.map((phase) => `<option value="${phase}">${PHASE_LABELS[phase]}</option>`).join("")}
    </select>
    <button id="batchRerunBtn">批量重跑</button>
    <button id="batchPackageBtn">打包下载</button>
    <button id="batchDeleteBtn" class="danger-button">删除</button>
  `;
  byId<HTMLButtonElement>("selectVisibleBtn").addEventListener("click", () => {
    filteredVideos().forEach((video) => selectedIds.add(video.id));
    renderListView();
  });
  byId<HTMLButtonElement>("clearSelectedBtn").addEventListener("click", () => {
    selectedIds.clear();
    renderListView();
  });
  byId<HTMLButtonElement>("batchRerunBtn").addEventListener("click", () => void batchRerun());
  byId<HTMLButtonElement>("batchDeleteBtn").addEventListener("click", () => void batchDelete());
  byId<HTMLButtonElement>("batchPackageBtn").addEventListener("click", () => void packageVideos(Array.from(selectedIds)));
}

async function batchRerun(): Promise<void> {
  if (selectedIds.size === 0) return;
  const phase = byId<HTMLSelectElement>("batchPhase").value;
  await api<{ results: BatchResult[] }>("/api/videos/batch/rerun", {
    method: "POST",
    body: JSON.stringify({ video_ids: Array.from(selectedIds), phase }),
  });
  await refresh({ autoSelect: false });
}

async function batchDelete(): Promise<void> {
  if (selectedIds.size === 0) return;
  if (!window.confirm(`确定删除 ${selectedIds.size} 个资源？本地视频和产物目录也会删除。`)) return;
  await api<{ results: BatchResult[] }>("/api/videos/batch/delete", {
    method: "POST",
    body: JSON.stringify({ video_ids: Array.from(selectedIds) }),
  });
  selectedIds.clear();
  await refresh({ autoSelect: false });
}

async function packageVideos(videoIds: string[]): Promise<void> {
  if (videoIds.length === 0) return;
  const result = await api<{ path: string; download_url: string }>("/api/package", {
    method: "POST",
    body: JSON.stringify({ video_ids: videoIds }),
  });
  window.location.href = result.download_url;
}

async function refresh(options: { autoSelect: boolean } = { autoSelect: false }): Promise<void> {
  const data = await api<{ videos: VideoItem[] }>("/api/videos");
  videos = data.videos;
  selectedIds.forEach((id) => {
    if (!videos.some((video) => video.id === id)) selectedIds.delete(id);
  });
  if (selectedId && !videos.some((video) => video.id === selectedId)) selectedId = "";
  if (options.autoSelect && !selectedId && videos.length > 0) selectedId = videos[0].id;
  renderListView();
}

// --- Safe stubs (will be fully replaced in Task 7) ---

function renderVideos(): void {
  // replaced by renderListView in Task 5-6; kept as stub for old call sites
}

function clearSelection(): void {
  selectedId = "";
  selectedIds.clear();
}

function seconds(value: number): string {
  const minutes = Math.floor(value / 60);
  const secs = Math.floor(value % 60);
  return `${minutes}:${secs.toString().padStart(2, "0")}`;
}

function renderRerunPhaseOptions(video: VideoItem): void {
  const phases = video.content_type === "question" ? QUESTION_PHASES : KNOWLEDGE_PHASES;
  byId<HTMLSelectElement>("rerunPhase").innerHTML = phases
    .map((phase) => `<option value="${phase}">${PHASE_LABELS[phase]}</option>`)
    .join("");
}

function renderDetailView(): void {
  byId<HTMLDivElement>("listView").classList.toggle("hidden", activeView !== "list");
  byId<HTMLDivElement>("detailView").classList.toggle("hidden", activeView !== "detail");
}

async function selectVideo(id: string): Promise<void> {
  selectedId = id;
  activeView = "detail";
  const video = videos.find((item) => item.id === id);
  if (!video) return;
  selectedType = video.content_type;
  byId<HTMLHeadingElement>("detailTitle").textContent = video.title;
  byId<HTMLParagraphElement>("detailSubtitle").textContent =
    `${TYPE_LABELS[video.content_type]} · ${video.external_id || "未填 ID"} · ${PHASE_LABELS[video.current_phase] ?? video.current_phase} · ${STATUS_LABELS[statusGroup(video)]}`;
  renderRerunPhaseOptions(video);
  renderDetailView();
}

function renderArtifacts(artifacts: VideoArtifacts): void {
  // will be replaced in Task 7; kept as stub
}

function connectAgentsWs(): void {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/api/agents`);
  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data) as AgentStatus[];
      agents = data;
      renderAgents();
    } catch {
      // ignore
    }
  };
  ws.onclose = () => {
    setTimeout(connectAgentsWs, 3000);
  };
}

// Event listeners
byId<HTMLButtonElement>("refreshBtn").addEventListener("click", () => void refresh({ autoSelect: false }));
byId<HTMLButtonElement>("selectModeBtn").addEventListener("click", () => {
  selectMode = !selectMode;
  selectedIds.clear();
  renderListView();
});
byId<HTMLButtonElement>("addOpenBtn").addEventListener("click", () => {
  renderAddDialogType();
  byId<HTMLDialogElement>("addDialog").showModal();
});
byId<HTMLButtonElement>("addCloseBtn").addEventListener("click", () => byId<HTMLDialogElement>("addDialog").close());
byId<HTMLDivElement>("typeTabs").querySelectorAll<HTMLButtonElement>("[data-type]").forEach((button) => {
  button.addEventListener("click", () => {
    selectedType = (button.dataset.type as ContentType) ?? "knowledge";
    selectedIds.clear();
    renderListView();
  });
});
byId<HTMLDialogElement>("addDialog").querySelectorAll<HTMLButtonElement>("[data-add-type]").forEach((button) => {
  button.addEventListener("click", () => {
    addContentType = (button.dataset.addType as ContentType) ?? "knowledge";
    renderAddDialogType();
  });
});
byId<HTMLInputElement>("searchInput").addEventListener("input", (event) => {
  searchQuery = (event.target as HTMLInputElement).value;
  renderListView();
});
byId<HTMLSelectElement>("statusFilter").addEventListener("change", (event) => {
  statusFilter = (event.target as HTMLSelectElement).value;
  renderListView();
});

byId<HTMLFormElement>("addForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId<HTMLTextAreaElement>("resourceIdsInput");
  const ids = input.value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (ids.length === 0) return;
  const response = await api<{ videos: VideoItem[]; results: AddResult[] }>("/api/videos", {
    method: "POST",
    body: JSON.stringify({
      items: ids.map((externalId) => ({ content_type: addContentType, external_id: externalId })),
    }),
  });
  renderAddResults(response.results);
  input.value = "";
  await refresh({ autoSelect: false });
});

byId<HTMLButtonElement>("backBtn").addEventListener("click", () => {
  activeView = "list";
  renderListView();
});
byId<HTMLButtonElement>("rerunBtn").addEventListener("click", async () => {
  if (!selectedId) return;
  const phase = byId<HTMLSelectElement>("rerunPhase").value;
  await api(`/api/videos/${selectedId}/rerun`, { method: "POST", body: JSON.stringify({ phase }) });
  await refresh({ autoSelect: false });
});
byId<HTMLButtonElement>("detailPackageBtn").addEventListener("click", () => {
  if (selectedId) void packageVideos([selectedId]);
});
byId<HTMLButtonElement>("deleteBtn").addEventListener("click", async () => {
  if (!selectedId) return;
  const video = videos.find((item) => item.id === selectedId);
  const label = video ? `${video.title} (${video.id})` : selectedId;
  if (!window.confirm(`确定删除 ${label}？本地视频和处理产物目录也会删除。`)) return;
  try {
    await api(`/api/videos/${selectedId}`, { method: "DELETE" });
    videos = videos.filter((item) => item.id !== selectedId);
    selectedId = "";
    clearSelection();
    activeView = "list";
    await refresh({ autoSelect: false });
  } catch (error) {
    window.alert(error instanceof Error ? error.message : "删除失败");
  }
});

void refresh();
connectAgentsWs();
