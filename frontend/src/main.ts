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
};

type VideoArtifacts = {
  subtitles: Array<{ index: number; start: number; end: number; text: string }>;
  chapters: Array<{ id: string; start_time: number; end_time: number; title: string }>;
  interactions: Array<Record<string, unknown>>;
  metadata: Record<string, unknown> | null;
};

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) {
  throw new Error("App root not found");
}

let videos: VideoItem[] = [];
let selectedId = "";
let agents: AgentStatus[] = [];

app.innerHTML = `
  <main class="shell">
    <aside class="sidebar">
      <header class="brand">
        <h1>Video Hive</h1>
        <button id="refreshBtn" class="icon-button" title="刷新">↻</button>
      </header>
      <form id="addForm" class="add-form">
        <div class="segmented">
          <label><input type="radio" name="contentType" value="knowledge" checked /> 知识点</label>
          <label><input type="radio" name="contentType" value="question" /> 题目解析</label>
        </div>
        <input id="externalIdInput" placeholder="知识点 code 或题目 ID" />
        <textarea id="urlInput" placeholder="视频链接，可留空；多条链接每行一个"></textarea>
        <input id="titleInput" placeholder="标题，可选" />
        <button type="submit">加入队列</button>
      </form>
      <div id="videoList" class="video-list"></div>
    </aside>
    <section class="workspace">
      <header class="toolbar">
        <div>
          <h2 id="title">选择一个视频</h2>
          <p id="subtitle">队列、阶段和预览会显示在这里</p>
        </div>
        <div class="actions">
          <select id="rerunPhase">
            <option value="download">下载</option>
            <option value="transcribe">转录</option>
            <option value="subtitle_review">字幕 review</option>
            <option value="chapter_generate">章节生成</option>
            <option value="interaction_generate" data-knowledge-only="true">互动生成</option>
            <option value="content_review" data-knowledge-only="true">内容 review</option>
            <option value="assemble">组装</option>
          </select>
          <button id="rerunBtn">重跑</button>
          <button id="deleteBtn" class="danger-button">删除</button>
          <button id="packageBtn">打包完成项</button>
        </div>
      </header>
      <div id="agentPanel" class="agent-panel"></div>
      <div class="content-grid">
        <section class="preview">
          <video id="player" controls></video>
          <div id="overlay" class="overlay hidden"></div>
        </section>
        <section class="panel">
          <h3>阶段</h3>
          <div id="phasePanel" class="phase-panel"></div>
          <h3>日志</h3>
          <pre id="logPanel"></pre>
        </section>
      </div>
      <section class="artifact-grid">
        <div>
          <h3>字幕</h3>
          <div id="subtitlesPanel" class="scroll-panel"></div>
        </div>
        <div>
          <h3>章节</h3>
          <div id="chaptersPanel" class="scroll-panel"></div>
        </div>
        <div>
          <h3>互动</h3>
          <div id="interactionsPanel" class="scroll-panel"></div>
        </div>
      </section>
    </section>
  </main>
`;

const byId = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element #${id}`);
  return el as T;
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) throw new Error(await response.text());
  return (await response.json()) as T;
}

function renderAgents(): void {
  const panel = byId<HTMLDivElement>("agentPanel");
  if (agents.length === 0) {
    panel.innerHTML = "";
    return;
  }
  panel.innerHTML = agents
    .map(
      (agent) => `
        <div class="agent-card ${agent.busy ? "busy" : "idle"}">
          <span class="agent-dot"></span>
          <span class="agent-name">${agent.name}</span>
          <span class="agent-status">${agent.busy ? "处理中" : "空闲"}</span>
          ${agent.current_video_id ? `<span class="agent-video">${agent.current_video_id}</span>` : ""}
        </div>
      `,
    )
    .join("");
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

function renderVideos(): void {
  const list = byId<HTMLDivElement>("videoList");
  list.innerHTML = videos
    .map(
      (video) => `
        <button class="video-row ${video.id === selectedId ? "active" : ""}" data-id="${video.id}">
          <span>${video.title}</span>
          <small>${video.content_type === "question" ? "题目" : "知识点"} · ${video.external_id || "未填ID"} · ${video.current_phase} · ${video.status}</small>
        </button>
      `,
    )
    .join("");
  list.querySelectorAll<HTMLButtonElement>(".video-row").forEach((button) => {
    button.addEventListener("click", () => selectVideo(button.dataset.id ?? ""));
  });
}

async function refresh(options: { autoSelect: boolean } = { autoSelect: true }): Promise<void> {
  const data = await api<{ videos: VideoItem[] }>("/api/videos");
  videos = data.videos;
  if (selectedId && !videos.some((video) => video.id === selectedId)) {
    selectedId = "";
    clearSelection();
  }
  renderVideos();
  if (options.autoSelect && !selectedId && videos.length > 0) {
    await selectVideo(videos[0].id);
  }
}

function clearSelection(): void {
  byId<HTMLHeadingElement>("title").textContent = "选择一个视频";
  byId<HTMLParagraphElement>("subtitle").textContent = "队列、阶段和预览会显示在这里";
  byId<HTMLVideoElement>("player").removeAttribute("src");
  byId<HTMLVideoElement>("player").load();
  byId<HTMLDivElement>("phasePanel").innerHTML = "";
  byId<HTMLPreElement>("logPanel").textContent = "";
  byId<HTMLDivElement>("subtitlesPanel").innerHTML = "";
  byId<HTMLDivElement>("chaptersPanel").innerHTML = "";
  byId<HTMLDivElement>("interactionsPanel").innerHTML = "";
}

function seconds(value: number): string {
  const minutes = Math.floor(value / 60);
  const secs = Math.floor(value % 60);
  return `${minutes}:${secs.toString().padStart(2, "0")}`;
}

async function selectVideo(id: string): Promise<void> {
  selectedId = id;
  const video = videos.find((item) => item.id === id);
  if (!video) return;
  renderVideos();
  byId<HTMLHeadingElement>("title").textContent = video.title;
  byId<HTMLParagraphElement>("subtitle").textContent =
    `${video.id} · ${video.content_type === "question" ? "题目解析" : "知识点"} · ${video.external_id || "未填ID"} · ${video.current_phase} · ${video.status}`;
  const player = byId<HTMLVideoElement>("player");
  player.src = video.source_url ? `/api/videos/${video.id}/video` : "";
  const phaseList =
    video.content_type === "question"
      ? ["download", "transcribe", "subtitle_review", "chapter_generate", "assemble"]
      : [
    "download",
    "transcribe",
    "subtitle_review",
    "chapter_generate",
    "interaction_generate",
    "content_review",
    "assemble",
        ];
  byId<HTMLDivElement>("phasePanel").innerHTML = phaseList
    .map((phase) => `<div class="${phase === video.current_phase ? "phase active" : "phase"}">${phase}</div>`)
    .join("");
  byId<HTMLSelectElement>("rerunPhase")
    .querySelectorAll<HTMLOptionElement>("[data-knowledge-only]")
    .forEach((option) => {
      option.disabled = video.content_type === "question";
    });
  const artifacts = await api<VideoArtifacts>(`/api/videos/${video.id}/artifacts`);
  renderArtifacts(artifacts);
  const logs = await api<{ log: string }>(`/api/videos/${video.id}/logs`);
  byId<HTMLPreElement>("logPanel").textContent = logs.log || "暂无日志";
}

function renderArtifacts(artifacts: VideoArtifacts): void {
  byId<HTMLDivElement>("subtitlesPanel").innerHTML = artifacts.subtitles
    .map((s) => `<button data-time="${s.start}"><time>${seconds(s.start)}</time>${s.text}</button>`)
    .join("");
  byId<HTMLDivElement>("chaptersPanel").innerHTML = artifacts.chapters
    .map((c) => `<button data-time="${c.start_time}"><time>${seconds(c.start_time)}</time>${c.title}</button>`)
    .join("");
  byId<HTMLDivElement>("interactionsPanel").innerHTML = artifacts.interactions
    .map((node) => `<button data-time="${Number(node.trigger_time ?? 0)}">${String(node.type ?? "interaction")}</button>`)
    .join("") || "不适用或暂无互动内容";

  document.querySelectorAll<HTMLButtonElement>("[data-time]").forEach((button) => {
    button.addEventListener("click", () => {
      byId<HTMLVideoElement>("player").currentTime = Number(button.dataset.time ?? 0);
    });
  });
}

byId<HTMLFormElement>("addForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId<HTMLTextAreaElement>("urlInput");
  const contentType = document.querySelector<HTMLInputElement>("input[name='contentType']:checked")?.value ?? "knowledge";
  const externalId = byId<HTMLInputElement>("externalIdInput").value.trim();
  const title = byId<HTMLInputElement>("titleInput").value.trim();
  const urls = input.value
    .split("\n")
    .map((url) => url.trim())
    .filter(Boolean);
  const items =
    urls.length > 0
      ? urls.map((url) => ({ url, title, content_type: contentType, external_id: externalId }))
      : [{ url: "", title, content_type: contentType, external_id: externalId }];
  if (!externalId && urls.length === 0) return;
  await api("/api/videos", { method: "POST", body: JSON.stringify({ items }) });
  input.value = "";
  byId<HTMLInputElement>("externalIdInput").value = "";
  byId<HTMLInputElement>("titleInput").value = "";
  await refresh();
});

byId<HTMLButtonElement>("refreshBtn").addEventListener("click", () => void refresh());
byId<HTMLButtonElement>("rerunBtn").addEventListener("click", async () => {
  if (!selectedId) return;
  const phase = byId<HTMLSelectElement>("rerunPhase").value;
  await api(`/api/videos/${selectedId}/rerun`, {
    method: "POST",
    body: JSON.stringify({ phase }),
  });
  await refresh();
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
    renderVideos();
    await refresh({ autoSelect: false });
  } catch (error) {
    window.alert(error instanceof Error ? error.message : "删除失败");
  }
});
byId<HTMLButtonElement>("packageBtn").addEventListener("click", async () => {
  const result = await api<{ path: string }>("/api/package", { method: "POST", body: "{}" });
  byId<HTMLPreElement>("logPanel").textContent = `已创建打包文件: ${result.path}`;
});

void refresh();
connectAgentsWs();
