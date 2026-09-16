# Release Notes 与 CHANGELOG 排版规范

本仓库三条产品线的发布说明（GitHub Release 正文）与 CHANGELOG 的写作、排版与发布操作规范。目标：release 页面与 CHANGELOG 在 GitHub 网页端渲染整洁、信息密度分层、三条产品线风格统一。

## 为什么需要这份规范

GitHub 对段落内**单个换行**的渲染分两种表面，窄幅手工断行的文本在两者上都会劣化：

- Release 正文（同 issue/PR 评论）：单换行渲染为换行（`<br>`）——按约 32 个汉字宽断行的段落，渲染出来是参差的断口，句子被随机切断，悬挂缩进全部丢失；
- 文件视图（CHANGELOG.md、docs）：单换行折叠为空格——中文句子中间被随机插入半角空格。

这是 2026-09 之前 release 页面观感混乱的直接根因；CHANGELOG 的窄幅段落贴进 release 正文时，断口原样带过去。2026-09-16 已对存量 Release（11 个）与 CHANGELOG（0.1.0–0.7.12 段）做过一次全量重排，本文是防止回潮的规范。

## 排版红线（硬规则）

1. **一条 bullet 一个逻辑行**。CHANGELOG 与 release 正文的每个条目写成单独一行，禁止在句中手工断行，禁止行尾双空格强制换行。中文不需要断行——浏览器自动软换行；源文件里的长行用编辑器 soft-wrap 查看（`:set wrap`），不要为此改文本。
2. **嵌套 bullet 缩进 2 空格**，续行一律拼回该 bullet，不留 4 空格悬挂缩进。
3. issue / PR 引用直接写 `#123`，GitHub 自动渲染成链接；不要手写完整 URL（Full Changelog 对比链接除外）。
4. 代码、命令、路径用反引号包裹；行内全角标点贴邻不加空格。

## Release 标题

统一为「产品名 + tag + 一句话主题」：

```
agent-legion v0.7.12 — 执行平面收尾与系统性还债
agent-legion worker v0.7.12 — 迟到心跳分流与事件落盘
velites v0.5.4 — 契约三档回落与迁移桥 deprecation
```

标题不复读 tag 名，也不写日期（release 页面自带 tag 与时间）。

## Release 正文模板

```markdown
<一句话主题段：本版是什么 + 关键词 + 指向 CHANGELOG 深记录。>

### Fixed
- <什么坏了>（#issue）：<根因一句话>，<修法与影响>。

### Changed
- <什么变了>（#issue）：<对用户的影响>。

### 升级注意
- <数据库迁移 / 配置变更 / 配套升级要求 / 退役公告>。

**Full Changelog**: https://github.com/LuciusCao/agent-legion/compare/<上一个tag>...<本tag>
```

- 分组沿用 Keep a Changelog 的 Added / Changed / Fixed / Performance / Deprecated / Maintenance（Observability 等扩展组按需），空组不保留。
- 每条 1–3 行：什么变了 + 对用户的影响 + issue 号。**不写实现细节**（锁序、迁移号、内部路径、触发器机制）——那些属于 CHANGELOG 与 issue，release 引用即可。
- 正文首行不写 `## [版本] - 日期` 标题（与页面元信息重复）。
- 有破坏性变更、数据库迁移、Host/Worker 配套要求时，必须单列「升级注意」组，写清动作与后果，不要埋进条目里。

### 反例与正例

反例（v0.7.12 原稿，硬换行 + 实现细节淹没重点）：

```markdown
- 状态计数触发器的跨语句死锁环（issue #659，v82 迁移）：v77（#437）
  只修了单条语句内的锁序——claim 批量在一个事务内逐条 promote
  （psycopg executemany），事务多次触发计数触发器，每次各自按
  (key, status) 排序取计数行锁，跨事务的锁集合序列随业务序变化……
```

正例（用户视角，一条逻辑行）：

```markdown
- 状态计数触发器跨语句死锁（#659，含 v82 迁移）：claim/心跳/rerun 间歇性 500 的根因，触发器入口改为分层 advisory lock 并统一锁序；残留毫秒级窗口由重试吸收。
```

## 内容分层：release 写摘要，CHANGELOG 写深记录

同一版本的两个层次，不要复制粘贴：

| 载体 | 受众 | 内容 |
|------|------|------|
| Release 正文 | 使用者、升级决策者 | 摘要：什么变了、影响、升级注意、对比链接 |
| CHANGELOG 段 | 深挖细节的维护者 | 完整工程记录：根因分析、修法取舍、边界与残留窗口 |

CHANGELOG 条目可以长（但仍是一条逻辑行），release 的对应条目是它的 1–3 行压缩。

## 三条产品线的差异

- **主仓 `v*`**（host/整仓）：手写正文，按上述模板；notes 取材自 CHANGELOG 对应段落的压缩改写。
- **`worker-v*`**：一段摘要（本版要点 + 配套 host 版本）+ 升级注意 + 指向主仓 release 的深记录，不重复展开。Worker 与 Host 有协议契约（心跳响应 shape、并发上限等），配套要求必须写明。
- **`velites-v*`**：由 `velites-release.yml` 自动生成——按 `velites/` 子树过滤 commit，剔除 merge commit 与 `chore(release)` 落版 commit，按 conventional 前缀分组（feat → Added、fix → Fixed、perf → Performance、其余 → Maintenance），条目为「主题（去掉 type(scope) 前缀）+ 短 SHA 链接」；标题取 `velites v<版本>`，落版 commit 主题带 `——<主题>` 后缀时自动拼接为标题。落版 PR 的标题写好一句话主题，它就是该版本的最佳摘要素材。

## 发布操作

```bash
# 主仓：notes 先写成文件再创建，避免 shell 转义问题
gh release create vX.Y.Z -R LuciusCao/agent-legion \
  --title "agent-legion vX.Y.Z — <一句话主题>" \
  --notes-file <notes.md>

# 改已有 release 的标题/正文
gh release edit vX.Y.Z -R LuciusCao/agent-legion \
  --title "<新标题>" --notes-file <notes.md>
```

- Full Changelog 链接区间用**同产品线的上一个 tag**（worker 对 worker、velites 对 velites），不要跨线。
- velites 版本线独立于仓库版本（`scripts/check_versions.py` 强制解耦），tag 必须与 `velites/Cargo.toml` 一致（workflow validate job 强制）。
- `.github/release.yml` 配置了 GitHub 原生自动 notes 的 label → 分组映射（bug → 修复、enhancement → 新增等），作为 `--generate-notes` 的兜底格式；主仓正式 release 仍应手写摘要。

## 检查清单（发版前过一遍）

- [ ] 标题符合「产品名 + tag + 一句话主题」。
- [ ] 正文开头是一句话主题段，无 `## [版本] - 日期` 冗余头。
- [ ] 每条 bullet 是一个逻辑行（源文件无句中换行、无行尾双空格）。
- [ ] 每条 1–3 行，无实现细节倾倒；issue 引用用 `#` 短格式。
- [ ] 有迁移 / 配套升级 / 退役时，「升级注意」组单列。
- [ ] 文末有 Full Changelog 链接，区间是同线上一 tag。
- [ ] CHANGELOG 对应段落已落版（含 Unreleased 归位）。
