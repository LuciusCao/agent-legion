# 文档漂移治理：现行文档退役术语基线（已实施）

状态：**已实施**（落地 PR 见本文件 git 历史；方案原文的备选论证与实施步骤已随落地瘦身，
保留本文作为机制说明与维护指引）。
背景：develop→main release train **#360**（2026-09-01）的文档 review 发现现行文档存在
退役术语残留（`docs/architecture/README.md` 总览图的 pipeline nodes / external skills、
README 的"外部 skill + 锁定 commit"旧语义等），而 `generate_architecture --check`
只护 AUTO-GENERATED 段落——散文与图表是盲区。每个大 PR 退役一批概念，
"公告写了"≠"所有现行文档都改了"，本检查把这类复发拦在门禁。

## 1. 机制

`scripts/architecture/docs_retired_terms.py`（挂 `check_repository`，随
`check_architecture` 在 quick/full/CI 各 lane 执行）：

- **扫描对象**：`_CURRENT_DOCS` 白名单（23 份现行文档）里的 markdown 正文。
  时点快照（`risk-review-*`、`velites-poc-*`、`docs/reviews/`）与本提案文件
  不在名单内，天然豁免。
- **违规定义**：白名单文档出现 `config/architecture/docs-retired-terms.yaml`
  里的禁用 pattern（regex，IGNORECASE），且命中上下文（前后各 1 行）不含
  退役表述词（`已退役|退役|不再|已删除|历史|legacy|retired|removed|…`）——
  同 `broad_except_audit.py` 的审计注释放行语义。
- **CHANGELOG 不入白名单**：版本段落按定义描述"当时的变化"，退役项合法
  高频出现（dry-run 实测结论）。
- **索引对账**（同检查附带）：`_CURRENT_DOCS` 中 `docs/architecture/` 部分
  必须与 `docs/architecture/README.md` "现行文档"索引表双向一致——白名单
  文档未登记（或被归进"历史设计记录"）即 error；索引表现行文档缺白名单
  条目同样 error。
- **已知盲区**（记录于评审，接受）：豁免词出现在同句但语义不覆盖目标词时
  误放行（实测样例：「自身不再携带 skill……在外部 skill 仓库改内容」——
  「不再」修饰 demo Agent 而非"外部仓库"）。词窗是成本/误报复衷，残余
  漏网靠 review 兜底；漏网率不可接受时升级为"豁免词须与目标词同分句"的窄窗。

## 2. 维护纪律

- **概念退役 PR**：同步在 `config/architecture/docs-retired-terms.yaml` 追加
  pattern 条目并清零现行文档命中（AGENTS.md §5 红线）。
- **pattern 设计**：只禁概念性表述，禁止匹配活代码路径——`executors/` 包、
  `worker/executor.py` 模块、`executors/leases.py` 都在服役，`executor` 单词
  本身不可入表；组合词（`executor definition/binding/allocation`）可以。
- **pattern 只增不删**；确需删除（概念复活）须在 yaml 注释记录 issue 依据
  （同 budgets JSON 的 git 锚点纪律）。误伤走 `exemptions`
  （`{path, term, reason, remove_when}`，与 `architecture-exemptions.yaml` 同构），
  不许删 pattern。
- **豁免到期检测**：nightly `exemption-expiry` job 读的是
  `architecture-exemptions.yaml`，不会自动读本 yaml——exemptions 非空时需
  把该 job 扩展到本文件（首版 `exemptions: []`，空转）。
- **白名单同步**：新增/移动现行文档时同步 `_CURRENT_DOCS` 与索引表
  （对账检查强制双向一致）；代码内嵌 agent playbook
  （`server/app/mcp_server/*.md`、`server/app/studio_chat/*.md`）刻意不在
  白名单（随 feature PR 高频重写、发布节奏独立）。

## 3. Quality Impact

- **gate 时长**：纯文本 regex 扫描（23 文档 × 8 pattern，<100ms），挂静态段，
  对各 lane 时长影响不可测；docs-only PR 不增加 lane。
- **误报面**：三层收窄——pattern 只禁概念组合词；退役表述词上下文豁免；
  CHANGELOG 与时点快照不扫。落地时全仓 dry-run 0 违规；残余误报走 exemptions。
- **测试范围**：`tests/scripts/test_architecture_docs_retired_terms.py`
  （18 用例：命中/豁免/邻行窗口/活代码路径不误伤/配置校验五类/白名单行为/
  索引对账），`@pytest.mark.no_db` 纯静态夹具，进 unit 层。
- **维护成本**：概念退役 PR 多两步（yaml 条目 + 清零命中），与 invariant
  registry 既有义务同量级。
