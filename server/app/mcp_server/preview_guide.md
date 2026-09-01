# Agent Legion Preview Panel Guide

How to author a workspace preview panel (issue #328): the job detail left
column renders one HTML+CSS+JS single-file bundle per workspace. Everything
you write is a DRAFT: a human previews it live in the job detail page and
publishes it there. Nothing you do takes effect in production by itself.

## 1. Tool map (in the order you typically need them)

- `get_preview_context(workspace_id, job_id=None)` — real data shapes: the
  workspace's recent jobs with their artifact inventories, plus bounded
  content samples (2k chars each, up to 5 artifacts) of one job — the given
  job_id, or the most recent one. Call this BEFORE writing any markup.
- `get_preview_panel(workspace_id)` — the workspace's current state: the
  published bundle (what users see) and any pending draft. Both null means
  the built-in fallback renders (the official question-panel bundle for
  question workspaces, the generic artifact list otherwise).
- `save_preview_panel_draft(workspace_id, html, change_note="")` — save a
  draft bundle. Validated: non-empty, a full HTML document (`<html`), at most
  256 KiB. Saving overwrites the existing draft. NEVER ask a human to
  "publish via API" — there is deliberately no publish tool.
- `get_preview_guide()` — this document. Local, always available.

## 2. The runtime contract

The bundle renders in `<iframe sandbox="allow-scripts">` — scripts run, but
the frame has an opaque origin: no cookies, no localStorage, no DOM access to
the host page, and `allow-same-origin` is NEVER granted (a same-origin script
bundle would be able to call every platform API with the viewer's session —
that is the security red line this design exists to avoid).

Consequences for your markup:

- Everything must be inline in the single HTML file: `<style>` and `<script>`
  blocks, no external origins. CDN references may be unreachable on
  self-hosted deployments — do not rely on them.
- Platform build assets the host explicitly offers (currently
  `assets.katexCssUrl` / `assets.katexJsUrl` for LaTeX) MAY be loaded; always
  degrade gracefully when absent.
- Network access from the bundle is untrusted output by definition: never
  `fetch()` the platform API directly (it fails — no credentials on an opaque
  origin — and is not the contract). Use the bridge.

## 3. Bridge API (postMessage, read-only)

All messages are plain JSON objects with a `source` marker; the panel's
origin is opaque, so the host identifies the frame by `event.source` and the
marker — do not rely on `event.origin`.

Panel → host (`source: "agent-legion-preview-panel"`):

- `{type: "ready"}` — send once at startup; the host answers with `init`.
- `{type: "request", id, method, params}` — call a bridge method:
  - `listArtifacts()` → `string[]` — artifact names of the current job.
  - `readArtifact({name})` → `{name, content}` — UTF-8 text of one artifact
    (same data as `GET /api/jobs/{id}/artifacts/{name}`).
  - `getJobDetail()` → the job detail payload (`job`, `nodes` with
    `node_key`/`status`, `runs`, `artifacts`) — use node statuses to gate
    sections by execution progress.
- `{type: "resize", height}` — ask the host to resize the frame (clamped to
  [120, 6000] px); a ResizeObserver on the document is the usual driver.

Host → panel (`source: "agent-legion-preview-host"`):

- `{type: "init", jobId, theme, assets}` — the panel's starting context.
  `theme` maps CSS custom property names to values (`--pp-bg`,
  `--pp-surface`, `--pp-text`, `--pp-text-secondary`, `--pp-accent`,
  `--pp-on-accent`, `--pp-error`, `--pp-border`, `--pp-radius`,
  `--pp-font-family`); apply them on `document.documentElement.style` so the
  panel follows the platform look. The host RE-SENDS `init` when node
  statuses change — treat every `init` as "re-fetch and re-render".
- `{type: "response", id, ok, payload | error}` — answer to a `request`.

Minimal client skeleton (copy and adapt):

```js
var PANEL_SOURCE = 'agent-legion-preview-panel'
var HOST_SOURCE = 'agent-legion-preview-host'
var seq = 0
var pending = {}
function callBridge(method, params) {
  return new Promise(function (resolve, reject) {
    var id = ++seq
    pending[id] = { resolve: resolve, reject: reject }
    window.parent.postMessage(
      { source: PANEL_SOURCE, type: 'request', id: id, method: method, params: params },
      '*'
    )
  })
}
window.addEventListener('message', function (event) {
  var data = event.data
  if (!data || data.source !== HOST_SOURCE) return
  if (data.type === 'init') { /* apply theme, (re)fetch, render */ }
  if (data.type === 'response' && pending[data.id]) {
    var entry = pending[data.id]
    delete pending[data.id]
    data.ok ? entry.resolve(data.payload) : entry.reject(new Error(data.error))
  }
})
window.parent.postMessage({ source: PANEL_SOURCE, type: 'ready' }, '*')
```

The platform ships a complete working example — the built-in question panel
bundle (`frontend/src/features/previewPanel/builtin/questionPanel.html` in
the repository). Read it before writing your own: it implements the full
bridge flow, artifact fallback chains, gate-by-node-status, and LaTeX
rendering with graceful degradation.

## 4. Authoring loop

1. `get_preview_context` — look at real artifacts of recent jobs; design the
   sections around what actually exists.
2. Write the single-file bundle; keep it small and self-contained.
3. `save_preview_panel_draft` — the human sees the draft rendered live in the
   left column while their "定制预览" dialog is open (only they see it).
4. Iterate with the human until they publish. On validation errors, fix and
   save again — the draft is overwritten, not versioned.
5. Hand off: publishing is always the human's click ("发布草稿"); the panel
   then renders for every viewer of that workspace's job detail pages.

## 5. Common errors

- HTTP 400 "must be a full HTML document": the bundle lacks `<html`; send a
  complete document, not a fragment.
- HTTP 400 size limit: the bundle exceeds 256 KiB — inline less, not more.
- Bridge calls never resolve: you forgot to send `ready`, or you are checking
  `event.origin` (it is always `"null"` for the sandboxed frame — check the
  `source` marker field instead).
- Blank panel after publish: you fetched the platform API directly instead of
  the bridge; the opaque origin carries no credentials.
