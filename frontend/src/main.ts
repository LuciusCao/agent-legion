import "./styles.css";

import { api } from "./api";
import { PHASE_LABELS, STATUS_LABELS, TYPE_LABELS } from "./labels";
import {
  filterVideos,
  parseResourceIds,
  statusGroup,
  visibleSelectedIds as getVisibleSelectedIds,
} from "./helpers";
import type {
  AddResult,
  AgentStatus,
  BatchResult,
  ContentType,
  DetailTab,
  VideoArtifacts,
  VideoItem,
  ViewName,
} from "./types";
import {
  hideOverlays,
  renderDetailView as renderPreviewDetailView,
  renderPlayer,
  renderRerunPhaseOptions,
  renderSentenceBox,
  showPractice,
  showSentence,
  updateChapterActiveClass,
  updatePlayInfo,
  updateSubtitleActiveClass,
} from "./views/detail";
import {
  renderAddDialogType,
  renderAddResults,
  renderListView as renderWorkbenchListView,
} from "./views/list";

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
let currentArtifacts: VideoArtifacts = { subtitles: [], chapters: [], interactions: [], metadata: null, review: null, checklist: null };
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
        <textarea id="resourceIdsInput" placeholder="一行一个知识点code，或者一行多个知识点用逗号分割"></textarea>
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

function filteredVideos(): VideoItem[] {
  return filterVideos(videos, {
    selectedType,
    statusFilter,
    searchQuery,
  });
}

function visibleSelectedIds(): string[] {
  return getVisibleSelectedIds(filteredVideos(), selectedIds);
}

function renderListView(): void {
  renderWorkbenchListView({
    activeView,
    agents,
    filteredVideos,
    onBatchDelete: () => void batchDelete(),
    onBatchPackage: () => void packageVideos(visibleSelectedIds()),
    onBatchRerun: () => void batchRerun(),
    onClearSelected: () => {
      selectedIds.clear();
      renderListView();
    },
    onSelectVisible: () => {
      filteredVideos().forEach((video) => selectedIds.add(video.id));
      renderListView();
    },
    onStatusFilterChange: (status) => {
      statusFilter = status;
      selectedIds.clear();
      renderListView();
    },
    onToggleVideoSelection: (id) => {
      if (selectedIds.has(id)) selectedIds.delete(id);
      else selectedIds.add(id);
      renderListView();
    },
    onVideoOpen: (id) => void selectVideo(id),
    searchQuery,
    selectMode,
    selectedId,
    selectedIds,
    selectedType,
    statusFilter,
    visibleSelectedIds,
    videos,
  });
}

async function batchRerun(): Promise<void> {
  const ids = visibleSelectedIds();
  if (ids.length === 0) return;
  const phase = byId<HTMLSelectElement>("batchPhase").value;
  const response = await api<{ results: BatchResult[] }>("/api/videos/batch/rerun", {
    method: "POST",
    body: JSON.stringify({ video_ids: ids, phase }),
  });
  showBatchResults("批量重跑", response.results);
  await refresh({ autoSelect: false });
}

async function batchDelete(): Promise<void> {
  const ids = visibleSelectedIds();
  if (ids.length === 0) return;
  if (!window.confirm(`确定删除 ${ids.length} 个资源？本地视频和产物目录也会删除。`)) return;
  const response = await api<{ results: BatchResult[] }>("/api/videos/batch/delete", {
    method: "POST",
    body: JSON.stringify({ video_ids: ids }),
  });
  showBatchResults("批量删除", response.results);
  selectedIds.clear();
  await refresh({ autoSelect: false });
}

function showBatchResults(title: string, results: BatchResult[]): void {
  const succeeded = results.filter((result) => ["deleted", "rerun"].includes(result.status)).length;
  const failed = results.filter((result) => !["deleted", "rerun"].includes(result.status));
  const details = failed
    .map((result) => `${result.video_id}: ${result.message || result.status}`)
    .join("\n");
  window.alert(`${title}完成：成功 ${succeeded} 项，失败 ${failed.length} 项${details ? `\n${details}` : ""}`);
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

function renderDetailView(): void {
  renderPreviewDetailView({
    activeTab,
    activeView,
    currentArtifacts,
    currentLog,
    onSeek: seekTo,
    onTabChange: (tab) => {
      activeTab = tab;
      renderDetailView();
    },
    selectedId,
    triggeredNodeIndexes,
    videos,
  });
}

async function selectVideo(id: string): Promise<void> {
  selectedId = id;
  activeView = "detail";
  triggeredNodeIndexes = new Set();
  currentSentence = [];
  const video = videos.find((item) => item.id === id);
  if (!video) return;
  selectedType = video.content_type;
  byId<HTMLHeadingElement>("detailTitle").textContent = video.title;
  byId<HTMLParagraphElement>("detailSubtitle").textContent =
    `${TYPE_LABELS[video.content_type]} · ${video.external_id || "未填 ID"} · ${PHASE_LABELS[video.current_phase] ?? video.current_phase} · ${STATUS_LABELS[statusGroup(video)]}`;
  renderRerunPhaseOptions(video);
  currentArtifacts = { subtitles: [], chapters: [], interactions: [], metadata: null, review: null, checklist: null };
  currentLog = "";
  renderPlayer(video, onTimeUpdate);
  renderDetailView();
  try {
    currentArtifacts = await api<VideoArtifacts>(`/api/videos/${video.id}/artifacts`);
    currentLog = (await api<{ log: string }>(`/api/videos/${video.id}/logs`)).log || "暂无日志";
  } catch (err) {
    currentLog = "加载资源失败";
  }
  activeTab = video.content_type === "question" ? "subtitles" : "nodes";
  renderDetailView();
}

function onTimeUpdate(): void {
  const player = document.getElementById("player") as HTMLVideoElement | null;
  if (!player) return;
  const time = player.currentTime;
  const subtitle = currentArtifacts.subtitles.find((item) => time >= item.start && time < item.end);
  const subtitleOverlay = document.getElementById("subtitleOverlay");
  if (subtitleOverlay) subtitleOverlay.textContent = subtitle?.text ?? "";
  currentArtifacts.interactions.forEach((node, index) => {
    const trigger = Number(node.trigger_time ?? 0);
    if (!triggeredNodeIndexes.has(index) && !player.paused && time >= trigger && time < trigger + 1.5) {
      showInteraction(index);
    }
  });
  updateChapterActiveClass(currentArtifacts.chapters, time);
  if (activeTab === "subtitles") updateSubtitleActiveClass(time);
  updatePlayInfo(currentArtifacts);
}

function seekTo(time: number): void {
  const player = document.getElementById("player") as HTMLVideoElement | null;
  if (player) player.currentTime = time;
}

function showInteraction(index: number): void {
  const node = currentArtifacts.interactions[index];
  const player = document.getElementById("player") as HTMLVideoElement | null;
  if (player && !player.paused) player.pause();
  triggeredNodeIndexes.add(index);
  const type = String(node.node_type ?? node.type ?? "");
  if (type === "example_practice") {
    showPractice(node, continueVideo);
  } else {
    currentSentence = [];
    showSentence(
      node,
      currentSentence,
      (word) => {
        currentSentence.push(word);
        renderSentenceBox(currentSentence);
      },
      () => {
        currentSentence = [];
        renderSentenceBox(currentSentence);
      },
      continueVideo,
    );
  }
  renderDetailView();
}

function continueVideo(): void {
  hideOverlays();
  const player = document.getElementById("player") as HTMLVideoElement | null;
  void player?.play();
}

function connectAgentsWs(): void {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/api/agents`);
  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data) as AgentStatus[];
      agents = data;
      renderListView();
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
  renderAddDialogType(addContentType);
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
    renderAddDialogType(addContentType);
  });
});
byId<HTMLInputElement>("searchInput").addEventListener("input", (event) => {
  searchQuery = (event.target as HTMLInputElement).value;
  selectedIds.clear();
  renderListView();
});
byId<HTMLSelectElement>("statusFilter").addEventListener("change", (event) => {
  statusFilter = (event.target as HTMLSelectElement).value;
  selectedIds.clear();
  renderListView();
});

byId<HTMLFormElement>("addForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId<HTMLTextAreaElement>("resourceIdsInput");
  const ids = parseResourceIds(input.value);
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
  const player = document.getElementById("player") as HTMLVideoElement | null;
  player?.pause();
  activeView = "list";
  renderListView();
});
byId<HTMLButtonElement>("rerunBtn").addEventListener("click", async () => {
  if (!selectedId) return;
  const phase = byId<HTMLSelectElement>("rerunPhase").value;
  try {
    await api(`/api/videos/${selectedId}/rerun`, { method: "POST", body: JSON.stringify({ phase }) });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("currently being processed")) {
      window.alert("该资源正在被处理中，请等待当前阶段完成后再重跑。");
      return;
    }
    throw err;
  }
  await refresh({ autoSelect: false });
  await selectVideo(selectedId);
});
byId<HTMLButtonElement>("detailPackageBtn").addEventListener("click", () => {
  if (selectedId) void packageVideos([selectedId]);
});
byId<HTMLButtonElement>("deleteBtn").addEventListener("click", async () => {
  if (!selectedId) return;
  if (!window.confirm("确定删除该资源？本地视频和处理产物目录也会删除。")) return;
  await api(`/api/videos/${selectedId}`, { method: "DELETE" });
  selectedId = "";
  activeView = "list";
  await refresh({ autoSelect: false });
});

void refresh();
connectAgentsWs();
