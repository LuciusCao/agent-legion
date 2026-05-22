import { KNOWLEDGE_PHASES, PHASE_LABELS, QUESTION_PHASES } from "../labels";
import { escapeHtml, getInteractionQuestion, seconds, statusGroup } from "../helpers";
import type { DetailTab, VideoArtifacts, VideoItem, ViewName } from "../types";

export type DetailViewContext = {
  activeTab: DetailTab;
  activeView: ViewName;
  currentArtifacts: VideoArtifacts;
  currentLog: string;
  onSeek: (time: number) => void;
  onTabChange: (tab: DetailTab) => void;
  selectedId: string;
  triggeredNodeIndexes: Set<number>;
  videos: VideoItem[];
};

const byId = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element #${id}`);
  return el as T;
};

export function renderRerunPhaseOptions(video: VideoItem): void {
  const phases = video.content_type === "question" ? QUESTION_PHASES : KNOWLEDGE_PHASES;
  byId<HTMLSelectElement>("rerunPhase").innerHTML = phases
    .map((phase) => `<option value="${phase}">${PHASE_LABELS[phase]}</option>`)
    .join("");
}

export function renderDetailView(ctx: DetailViewContext): void {
  byId<HTMLDivElement>("listView").classList.toggle("hidden", ctx.activeView !== "list");
  byId<HTMLDivElement>("detailView").classList.toggle("hidden", ctx.activeView !== "detail");
  renderChaptersStrip(ctx);
  renderDetailTabs(ctx);
  renderTabPanel(ctx);
  updatePlayInfo(ctx.currentArtifacts);
}

export function renderPlayer(video: VideoItem, onTimeUpdate: () => void): void {
  const wrap = byId<HTMLDivElement>("playerWrap");
  if (!video.source_url) {
    wrap.innerHTML = `<div class="empty-state">等待视频 URL</div>`;
    return;
  }
  wrap.innerHTML = `
    <video id="player" controls src="/api/videos/${video.id}/video"></video>
    <div class="subtitle-overlay"><div id="subtitleOverlay" class="subtitle-text"></div></div>
    <div id="practiceOverlay" class="practice-toast hidden"></div>
    <div id="sentenceOverlay" class="interaction-overlay hidden"></div>
  `;
  const player = byId<HTMLVideoElement>("player");
  player.addEventListener("timeupdate", onTimeUpdate);
  player.addEventListener("seeked", onTimeUpdate);
}

export function updateChapterActiveClass(chapters: VideoArtifacts["chapters"], time: number): void {
  byId<HTMLDivElement>("chaptersStrip").querySelectorAll<HTMLButtonElement>(".chapter-chip").forEach((btn, i) => {
    const ch = chapters[i];
    btn.classList.toggle("active", ch && time >= ch.start_time && time < ch.end_time);
  });
}

export function updateSubtitleActiveClass(time: number): void {
  byId<HTMLDivElement>("tabPanel").querySelectorAll<HTMLButtonElement>(".subtitle-row").forEach((btn) => {
    const start = Number(btn.dataset.time ?? 0);
    const end = Number(btn.dataset.end ?? Infinity);
    btn.classList.toggle("active", time >= start && time < end);
  });
}

export function hideOverlays(): void {
  document.getElementById("practiceOverlay")?.classList.add("hidden");
  document.getElementById("sentenceOverlay")?.classList.add("hidden");
}

export function showPractice(node: Record<string, unknown>, onContinue: () => void): void {
  hideOverlays();
  const question = getInteractionQuestion(node);
  const overlay = byId<HTMLDivElement>("practiceOverlay");
  overlay.innerHTML = `
    <div class="practice-card">
      <strong>例题试做</strong>
      <p>${escapeHtml(String(question.instruction ?? "请先独立完成这道题"))}</p>
      <small>${escapeHtml(String(question.hint ?? ""))}</small>
      <div><button id="practiceSkipBtn">跳过</button><button id="practiceContinueBtn" class="primary-button">继续播放</button></div>
    </div>
  `;
  overlay.classList.remove("hidden");
  byId<HTMLButtonElement>("practiceSkipBtn").addEventListener("click", onContinue);
  byId<HTMLButtonElement>("practiceContinueBtn").addEventListener("click", onContinue);
}

export function showSentence(
  node: Record<string, unknown>,
  currentSentence: string[],
  onWord: (word: string) => void,
  onReset: () => void,
  onContinue: () => void,
): void {
  hideOverlays();
  const question = getInteractionQuestion(node);
  const options = ((question.options ?? []) as Array<Record<string, unknown>>).map((option) => String(option.text ?? ""));
  const overlay = byId<HTMLDivElement>("sentenceOverlay");
  overlay.innerHTML = `
    <div class="sentence-card">
      <strong>视频总结</strong>
      <p>点击词语，按顺序组成句子</p>
      <div id="sentenceBox" class="sentence-box"></div>
      <div id="wordBank" class="word-bank">${options.map((word) => `<button data-word="${escapeHtml(word)}">${escapeHtml(word)}</button>`).join("")}</div>
      <div><button id="sentenceResetBtn">重置</button><button id="sentenceSkipBtn">跳过</button><button id="sentenceSubmitBtn" class="primary-button">提交</button></div>
    </div>
  `;
  overlay.classList.remove("hidden");
  overlay.querySelectorAll<HTMLButtonElement>("[data-word]").forEach((button) => {
    button.addEventListener("click", () => onWord(button.dataset.word ?? ""));
  });
  byId<HTMLButtonElement>("sentenceResetBtn").addEventListener("click", onReset);
  byId<HTMLButtonElement>("sentenceSkipBtn").addEventListener("click", onContinue);
  byId<HTMLButtonElement>("sentenceSubmitBtn").addEventListener("click", onContinue);
  renderSentenceBox(currentSentence);
}

export function renderSentenceBox(currentSentence: string[]): void {
  const box = document.getElementById("sentenceBox");
  if (box) box.textContent = currentSentence.join(" / ") || "尚未选择";
}

function renderChaptersStrip(ctx: DetailViewContext): void {
  byId<HTMLDivElement>("chaptersStrip").innerHTML = ctx.currentArtifacts.chapters
    .map((chapter) => {
      return `<button class="chapter-chip" data-time="${chapter.start_time}">${escapeHtml(chapter.title)}</button>`;
    })
    .join("");
  byId<HTMLDivElement>("chaptersStrip").querySelectorAll<HTMLButtonElement>("[data-time]").forEach((button) => {
    button.addEventListener("click", () => ctx.onSeek(Number(button.dataset.time ?? 0)));
  });
}

function availableTabs(ctx: DetailViewContext): Array<{ key: DetailTab; label: string }> {
  const video = ctx.videos.find((item) => item.id === ctx.selectedId);
  const tabs: Array<{ key: DetailTab; label: string }> = [
    { key: "subtitles", label: "字幕" },
    { key: "chapters", label: "章节" },
    { key: "logs", label: "日志" },
    { key: "metadata", label: "元数据" },
  ];
  if (video?.content_type !== "question") tabs.unshift({ key: "nodes", label: "互动节点" });
  return tabs;
}

function renderDetailTabs(ctx: DetailViewContext): void {
  byId<HTMLElement>("detailTabs").innerHTML = availableTabs(ctx)
    .map((tab) => `<button class="tab ${ctx.activeTab === tab.key ? "active" : ""}" data-tab="${tab.key}">${tab.label}</button>`)
    .join("");
  byId<HTMLElement>("detailTabs").querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => ctx.onTabChange((button.dataset.tab as DetailTab) ?? "subtitles"));
  });
}

function renderTabPanel(ctx: DetailViewContext): void {
  const panel = byId<HTMLDivElement>("tabPanel");
  if (ctx.activeTab === "nodes") {
    panel.innerHTML =
      ctx.currentArtifacts.interactions
        .map((node, index) => {
          const type = String(node.node_type ?? node.type ?? "interaction");
          const label = type === "example_practice" ? "例题试做" : "视频总结";
          return `
            <button class="node-card ${ctx.triggeredNodeIndexes.has(index) ? "answered" : ""}" data-node="${index}">
              <strong>${label}</strong>
              <span>${seconds(Number(node.trigger_time ?? 0))}</span>
              <small>${escapeHtml(String(node.chapter_id ?? ""))}</small>
            </button>
          `;
        })
        .join("") || `<div class="empty-state">暂无互动节点</div>`;
    panel.querySelectorAll<HTMLButtonElement>("[data-node]").forEach((button) => {
      const node = ctx.currentArtifacts.interactions[Number(button.dataset.node ?? 0)];
      button.addEventListener("click", () => ctx.onSeek(Number(node.trigger_time ?? 0)));
    });
    return;
  }
  if (ctx.activeTab === "subtitles") {
    panel.innerHTML = ctx.currentArtifacts.subtitles
      .map((subtitle) => {
        return `<button class="subtitle-row" data-time="${subtitle.start}" data-end="${subtitle.end}"><time>${seconds(subtitle.start)} - ${seconds(subtitle.end)}</time><span>${escapeHtml(subtitle.text)}</span></button>`;
      })
      .join("") || `<div class="empty-state">暂无字幕</div>`;
    panel.querySelectorAll<HTMLButtonElement>("[data-time]").forEach((button) => {
      button.addEventListener("click", () => ctx.onSeek(Number(button.dataset.time ?? 0)));
    });
    return;
  }
  if (ctx.activeTab === "chapters") {
    panel.innerHTML = ctx.currentArtifacts.chapters
      .map((chapter) => `<button class="subtitle-row" data-time="${chapter.start_time}"><time>${seconds(chapter.start_time)} - ${seconds(chapter.end_time)}</time><span>${escapeHtml(chapter.title)}</span></button>`)
      .join("") || `<div class="empty-state">暂无章节</div>`;
    panel.querySelectorAll<HTMLButtonElement>("[data-time]").forEach((button) => {
      button.addEventListener("click", () => ctx.onSeek(Number(button.dataset.time ?? 0)));
    });
    return;
  }
  if (ctx.activeTab === "logs") {
    panel.innerHTML = `<pre>${escapeHtml(ctx.currentLog)}</pre>`;
    return;
  }
  panel.innerHTML = `<pre>${escapeHtml(JSON.stringify(ctx.currentArtifacts.metadata ?? {}, null, 2))}</pre>`;
}

export function updatePlayInfo(artifacts: VideoArtifacts): void {
  const player = document.getElementById("player") as HTMLVideoElement | null;
  const time = player?.currentTime ?? 0;
  const chapter = artifacts.chapters.find((item) => time >= item.start_time && time < item.end_time);
  const subtitle = artifacts.subtitles.find((item) => time >= item.start && time < item.end);
  const nextNode = artifacts.interactions.find((node) => Number(node.trigger_time ?? 0) > time);
  byId<HTMLDivElement>("playInfoPanel").innerHTML = `
    <h2>当前播放信息</h2>
    <div class="info-row"><span>当前章节</span><strong>${escapeHtml(chapter?.title ?? "—")}</strong></div>
    <div class="info-row"><span>播放时间</span><strong>${seconds(time)}</strong></div>
    <div class="info-row"><span>下一个互动</span><strong>${nextNode ? seconds(Number(nextNode.trigger_time ?? 0)) : "无"}</strong></div>
    <div class="info-row"><span>当前字幕</span><p>${escapeHtml(subtitle?.text ?? "—")}</p></div>
  `;
}
