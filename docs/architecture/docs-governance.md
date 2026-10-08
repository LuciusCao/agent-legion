# 文档漂移治理：退役术语基线与事实一致性检查

两项静态检查把「代码变了、现行文档没跟上」拦在门禁：退役术语基线拦**已删除的概念**
重新以现行语义出现，事实一致性检查拦**仍存在但取值变了的事实**。
`generate_architecture --check` 只护 AUTO-GENERATED 段落，散文与图表靠这两项兜底。

两项都挂在 `check_repository`（`scripts/architecture/repository.py`），随
`check_architecture` 在 quick/full/CI 的 backend 静态轮执行；docs-only PR 跳过
backend lane 时，由 CI 的 `docs-terms` 轻量 job 以模块独立入口
（`python -m scripts.architecture.docs_retired_terms`，同时跑两项）补位，两条入口互补。

## 1. 退役术语基线（`scripts/architecture/docs_retired_terms.py`）

- **扫描对象**：`_CURRENT_DOCS` 白名单里的 markdown 正文（清单以源文件为准）。
  白名单外的文档天然豁免：时点快照（`risk-review-*`、`velites-poc-*` 等历史设计记录）、
  CHANGELOG（版本段落按定义描述"当时的变化"，退役项合法高频出现）。
- **显式豁免**：`_DOC_EXEMPT_PREFIXES`（整目录，当前为 `docs/reviews/`）与
  `_DOC_EXEMPT_FILES`（单文件：本文与
  [instance-settings-legacy-concepts-governance.md](instance-settings-legacy-concepts-governance.md)——
  治理文档必须**点名**它们治理的退役概念，命中是引用而非现行语义）。豁免文件可以登记在
  索引「现行文档」表里而不进白名单，对账检查对它们放行。
- **违规定义**：白名单文档出现 `config/architecture/docs-retired-terms.yaml` 里的
  禁用 pattern（regex，IGNORECASE），且命中上下文（命中行前后各 1 行）在遮掉命中本身后
  不含退役表述词（`_RETIREMENT_PHRASE`：`已退役|退役|不再|已删除|历史|legacy|retired|…`）——
  同 `broad_except_audit.py` 的审计注释放行语义。
- **索引对账**（同检查附带）：`_CURRENT_DOCS` 中 `docs/architecture/` 部分必须与
  [README.md](README.md)「现行文档」索引表双向一致——白名单文档未登记（或被归进
  「历史设计记录」）即 error；索引表现行文档缺白名单条目同样 error（豁免文件除外）。
  `docs/` 根下与仓库根的现行文档只进白名单，不参与对账。
- **已知盲区**（接受）：豁免词出现在同一词窗但修饰的是别的对象时误放行。残余漏网靠
  review 兜底；漏网率不可接受时升级为"豁免词须与目标词同分句"的窄窗。

## 2. 事实一致性（`scripts/architecture/docs_consistency.py`）

退役术语只能拦"词被删了"，拦不住"词还在、值变了"（如默认存储后端从 RustFS 换成
SeaweedFS 后 README 仍写旧默认）。本检查从活代码文件解析事实取值（不在检查里硬编码镜像），
与文档里的固定句式比对；断言的事实清单见该模块 docstring。源文件改名或句式改写导致解析
失败时 fail-closed（报"source file missing / cannot find"），而不是静默失效。

## 3. 维护纪律

- **概念退役 PR**：同步在 `config/architecture/docs-retired-terms.yaml` 追加 pattern
  条目并清零现行文档命中（AGENTS.md §5 红线）。
- **pattern 设计**：只禁概念性表述，禁止匹配活代码路径——`executors/` 包、
  `worker/executor.py` 模块、`executors/leases.py` 都在服役，`executor` 单词本身不可入表；
  组合词（`executor definition/binding/allocation`）可以。
- **pattern 只增不删**；确需删除（概念复活）须在 yaml 注释记录 issue 依据。误伤走
  `exemptions`（`{path, term, reason, remove_when}`，与 `architecture-exemptions.yaml`
  同构），不许删 pattern。nightly `exemption-expiry` job 只读
  `architecture-exemptions.yaml`；本 yaml 的 `exemptions` 非空时需把该 job 扩展过来。
- **白名单同步**：新增/移动/删除现行文档时同步 `_CURRENT_DOCS` 与索引表；文件改名或删除
  要 grep 全仓引用（含 `scripts/`、`tests/`、`config/`、代码注释）。代码内嵌 agent playbook
  （`server/app/mcp_server/*.md`、`server/app/studio_chat/*.md`）刻意不在白名单（随 feature
  PR 高频重写、发布节奏独立）。
- **新增事实断言**：在 `docs_consistency.py` 加解析函数 + 文档句式 pattern，并在
  `tests/scripts/test_architecture_docs_consistency.py` 补对应夹具；文档里被断言的句式改写时
  同步改 pattern。

## 4. Quality Impact

- **gate 时长**：纯文本 regex 扫描，挂静态段，对各 lane 时长影响不可测；docs-only PR
  只多一个轻量 job。
- **误报面**：pattern 只禁概念组合词；退役表述词上下文豁免；CHANGELOG 与时点快照不扫；
  残余误报走 exemptions。
- **测试范围**：`tests/scripts/test_architecture_docs_retired_terms.py` 与
  `tests/scripts/test_architecture_docs_consistency.py`（纯静态夹具，进 unit 层）。
