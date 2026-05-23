import { KNOWLEDGE_PHASES, PHASE_LABELS, QUESTION_PHASES, STATUS_LABELS, TYPE_LABELS } from "../labels";
import { escapeHtml, statusGroup } from "../helpers";
import type { AddResult, AgentStatus, ContentType, VideoItem, ViewName } from "../types";

export type ListViewContext = {
  activeView: ViewName;
  agents: AgentStatus[];
  filteredVideos: () => VideoItem[];
  onBatchDelete: () => void;
  onBatchPackage: () => void;
  onBatchRerun: () => void;
  onClearSelected: () => void;
  onSelectVisible: () => void;
  onStatusFilterChange: (status: string) => void;
  onToggleVideoSelection: (id: string) => void;
  onVideoOpen: (id: string) => void;
  searchQuery: string;
  selectMode: boolean;
  selectedId: string;
  selectedIds: Set<string>;
  selectedType: ContentType;
  statusFilter: string;
  visibleSelectedIds: () => string[];
  videos: VideoItem[];
};

const byId = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element #${id}`);
  return el as T;
};

export function renderListView(ctx: ListViewContext): void {
  byId<HTMLDivElement>("listView").classList.toggle("hidden", ctx.activeView !== "list");
  byId<HTMLDivElement>("detailView").classList.toggle("hidden", ctx.activeView !== "detail");
  byId<HTMLParagraphElement>("workbenchSubtitle").textContent = `${TYPE_LABELS[ctx.selectedType]}资源处理队列`;
  byId<HTMLDivElement>("typeTabs")
    .querySelectorAll<HTMLButtonElement>("[data-type]")
    .forEach((button) => button.classList.toggle("active", button.dataset.type === ctx.selectedType));
  renderStats(ctx);
  renderAgents(ctx.agents);
  renderBatchToolbar(ctx);
  renderGroupedList(ctx);
}

export function renderAddDialogType(addContentType: ContentType): void {
  byId<HTMLDialogElement>("addDialog")
    .querySelectorAll<HTMLButtonElement>("[data-add-type]")
    .forEach((button) => button.classList.toggle("active", button.dataset.addType === addContentType));
  byId<HTMLTextAreaElement>("resourceIdsInput").placeholder =
    addContentType === "knowledge"
      ? "一行一个知识点code，或者一行多个知识点用逗号分割"
      : "一行一个题目ID，或者一行多个题目用逗号分割";
}

export function renderAddResults(results: AddResult[]): void {
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

function renderStats(ctx: ListViewContext): void {
  const scoped = ctx.videos.filter((video) => video.content_type === ctx.selectedType);
  const counts = ["missing_url", "queued", "running", "failed", "completed"].map((key) => ({
    key,
    label: STATUS_LABELS[key],
    count: scoped.filter((video) => statusGroup(video) === key).length,
  }));
  byId<HTMLDivElement>("statsPanel").innerHTML = [
    `<button class="stat-card ${ctx.statusFilter === "all" ? "active" : ""}" data-status="all"><span>全部</span><strong>${scoped.length}</strong></button>`,
    ...counts.map(
      (item) =>
        `<button class="stat-card ${ctx.statusFilter === item.key ? "active" : ""}" data-status="${item.key}"><span>${item.label}</span><strong>${item.count}</strong></button>`,
    ),
  ].join("");
  byId<HTMLDivElement>("statsPanel").querySelectorAll<HTMLButtonElement>("[data-status]").forEach((button) => {
    button.addEventListener("click", () => {
      const status = button.dataset.status ?? "all";
      byId<HTMLSelectElement>("statusFilter").value = status;
      ctx.onStatusFilterChange(status);
    });
  });
}

function renderGroupedList(ctx: ListViewContext): void {
  const list = byId<HTMLDivElement>("groupedList");
  const items = ctx.filteredVideos();
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
            ${groupItems.map((video) => renderVideoRow(video, ctx)).join("") || `<div class="empty-row">暂无资源</div>`}
          </div>
        </section>
      `;
    })
    .join("");

  list.querySelectorAll<HTMLButtonElement>(".resource-row").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.id ?? "";
      if (ctx.selectMode) ctx.onToggleVideoSelection(id);
      else ctx.onVideoOpen(id);
    });
  });
}

function renderVideoRow(video: VideoItem, ctx: ListViewContext): string {
  const checked = ctx.selectedIds.has(video.id) ? "checked" : "";
  const agent = ctx.agents.find((item) => item.current_video_id === video.id);
  const error = video.error_message ? `<small class="row-error">${escapeHtml(video.error_message)}</small>` : "";
  return `
    <button class="resource-row ${ctx.selectedId === video.id ? "active" : ""}" data-id="${video.id}">
      ${ctx.selectMode ? `<span class="fake-check ${checked}"></span>` : ""}
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

function renderAgents(agents: AgentStatus[]): void {
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

function renderBatchToolbar(ctx: ListViewContext): void {
  const toolbar = byId<HTMLDivElement>("batchToolbar");
  toolbar.classList.toggle("hidden", !ctx.selectMode);
  if (!ctx.selectMode) return;
  const phases = ctx.selectedType === "question" ? QUESTION_PHASES : KNOWLEDGE_PHASES;
  const selectedVisibleCount = ctx.visibleSelectedIds().length;
  toolbar.innerHTML = `
    <span>已选 ${selectedVisibleCount} 项</span>
    <button id="selectVisibleBtn">全选当前结果</button>
    <button id="clearSelectedBtn">清空</button>
    <select id="batchPhase">
      ${phases.map((phase) => `<option value="${phase}">${PHASE_LABELS[phase]}</option>`).join("")}
    </select>
    <button id="batchRerunBtn">批量重跑</button>
    <button id="batchPackageBtn">打包下载</button>
    <button id="batchDeleteBtn" class="danger-button">删除</button>
  `;
  byId<HTMLButtonElement>("selectVisibleBtn").addEventListener("click", ctx.onSelectVisible);
  byId<HTMLButtonElement>("clearSelectedBtn").addEventListener("click", ctx.onClearSelected);
  byId<HTMLButtonElement>("batchRerunBtn").addEventListener("click", ctx.onBatchRerun);
  byId<HTMLButtonElement>("batchDeleteBtn").addEventListener("click", ctx.onBatchDelete);
  byId<HTMLButtonElement>("batchPackageBtn").addEventListener("click", ctx.onBatchPackage);
}
