# 产物身份协议：状态空间网格模型

分布式协议是这个项目的核心。产物身份协议（一个节点执行读谁家的字节、
校验谁家的字节、提升谁家的字节）横跨 dispatch、claim、Worker、
completion、GC 五个进程域，单点修复会按下葫芦浮起瓢——#876 五轮复审
（claim 比对 → 消费点回落 → keep-last → 对抗 F1-F5/A1 → codex P2×2）
每一轮都落在相邻格子上。本文把协议的完整状态空间显式化为网格：生命
周期六阶段 × 八变异轴 × 八不变量，每格三态钉死，由
`tests/scripts/test_artifact_identity_grid.py` 一致性检查防腐。

RELATED: [execution-generation.md](execution-generation.md) §2.11
（EXEC-INPUT-IDENTITY-001 的协议正文）、
[workspace-executor-evidence-matrix.md](workspace-executor-evidence-matrix.md)
（不变量 ↔ 证据的反向审计矩阵）。

## 1. 生命周期六阶段

| 阶段 | 定义 | 关键模块 |
|---|---|---|
| S1 dispatch 冻结 | `stage_agent_inputs` 把 Worker 将消费的 input 字节 put 进 CAS、按 (job,node) 持 ref 防 GC、`sha256:<digest>` 冻结进 DB manifest | `agent_broker/agent_artifacts.py` |
| S2 claim 变换 | claim 时把 CAS ref 做等价 transport 变换为 presigned GET（digest 比对守卫，memory-only 不落库） | `agent_broker/remote_artifact_support.py` |
| S3 Worker 物化 | 按 ref 形态下载（presigned/CAS 双 transport），digest 自验，失配/失败回落 CAS；重复规范化名按声明顺序物化、last wins | `worker/artifact/inputs.py` |
| S4 执行 | Worker 跑节点（ velites / code ），写 outputs | worker 执行域 |
| S5 completion | 解包归档、remote refs 验证/提升下载、产物 ref 登记（撞名守卫）、Host 侧声明视图校验 | `agent_control/completion_staged.py`、`workflows/validation_view.py` |
| S6 promotion | reconcile → 代次闸 → 提升 → manifest commit → 镜像 | `executors/_lease_finish_promotion.py`、`executors/artifact_mirror.py` |

## 2. 八变异轴与威胁模型

| 轴 | 取值 | 威胁模型 |
|---|---|---|
| C1 通道 | local code / remote refs / legacy archive | 三通道字节来源与登记路径不同，身份冻结必须在三通道同语义 |
| C2 形态 | plain / gzip-v4 / gzip-to-v3 | 压缩态与未压缩态的 digest 口径错配（口径恒为未压缩内容 sha256，三处同基准） |
| C3 输入身份供给 | 冻结 CAS ref / legacy 无 ref / blob 缺失 | 无 ref 是 #833 前 legacy 豁免（随旧 job 耗尽归零）；blob 缺失是 GC 竞态 fail-open 降级 |
| C4 存储后端 | 硬链 / 拷贝回落（reflink 优先） | 硬链共享 inode 让 validator 写穿越；拷贝回落的 I/O 成本 |
| C5 时序事件 | E1 dispatch→claim 重写 / E2 签发→GET 覆盖 / E3 池排队跨 GC tick / E4 校验→提升代次翻转 / E5 双读间重写 / E6 reclaim/requeue | 每个事件都是「两个读点之间世界变了」的 TOCTOU 族 |
| C6 validator 行为 | 不碰 / in-place / temp+replace / delete+recreate / chmod / 恶意写 input | 写模式族决定 reconcile 覆盖度；恶意写检验私有副本隔离 |
| C7 Worker 诚实度 | 如实 / 撞名上报 / 双通道同名 / 截断 manifest / 脏字节 | Worker 上报不可信，Host 全权复检 |
| C8 失败注入 | 403/404/5xx、解码失败、CAS 404、池崩溃、盘满、unlink 失败 | 每个失败点的分级语义（fail-closed / fail-open / 回落） |

## 3. 八不变量

| 编号 | 陈述 | 违反后果 |
|---|---|---|
| INV-1 | 输入身份唯一解析点在 dispatch；之后只允许等价 transport 变换，消费点 digest 自验闭环（EXEC-INPUT-IDENTITY-001） | Worker 跑新字节、Host 校旧字节，双向判错（#876 codex P1 两连发） |
| INV-2 | 校验视图隔离：validator 只见本节点声明 inputs + 本次声明 outputs，兄弟/陈旧/未声明/归档垃圾永不可达 | #757 跨文件对账误归因（A/B 互相翻盘） |
| INV-3 | 写隔离：input 一律私有副本（reflink/拷贝），CAS 来源字节同权 | validator 的写/chmod 穿越共享 inode 污染上游产物或 CAS blob |
| INV-4 | 提升字节 == 校验通过字节：outputs 全写模式族 reconcile 回 run view，finish 闸只提升验后字节 | 提升未清洗字节（review 族 clean-in-place 失效） |
| INV-5 | ref 生命周期覆盖「上传→校验→提升」全程：校验前登记（GC 防护）+ 撞名守卫（冻结 input 优先） | 零引用窗口 blob 被 GC 后 add_ref 撞 FK 即 500；撞名 upsert 把冻结 input 顶成孤儿 |
| INV-6 | 未修改判定不可伪造：hash 级（内容 sha256），永不用 mtime/size | utime 回拨 + 同尺寸改写的伪造绕过 reconcile，旧字节被提升 |
| INV-7 | 重复规范化名 last-wins：消费身份由 Worker 物化顺序定义，校验视图同向去重 | 校验第一个别名、Worker 消费最后一个别名，身份错位 |
| INV-8 | 失败语义分级：构造/placement 失败 fail-closed，缺源 fail-open，传输失败两段式归因 | 静默不完整视图被当成验过 / 残余竞态被当成业务失败 |

## 4. 矩阵本体

不做全笛卡尔积——「不变量 × 相关攻击轴」，轴与不变量无关的格不列。
每格三态：✅ 测试钉住（点名测试）、🧱 结构性不可达（必须附论证）、
⬜ 待办（**交付时不允许存在**；一致性检查拒绝 ⬜ 与任何第四态）。

| 不变量 | 轴 | 状态 | 证据 / 论证 |
|---|---|---|---|
| INV-1 | C1 | ✅ | `tests/services/test_agent_artifacts.py::test_stage_agent_inputs_uploads_inputs_and_rewrites_manifest`（冻结机制）、`tests/services/test_agent_artifact_inject.py::test_inject_adds_uploads_and_upgrades_staged_inputs`（agent 道）、`tests/services/test_agent_artifact_inject.py::test_code_claim_rebuild_injects_object_block`（code 道）、`tests/services/test_agent_artifact_inject.py::test_inject_degrades_to_legacy_channel_on_storage_error`（legacy 降级） |
| INV-1 | C2 | ✅ | `tests/services/test_agent_artifact_inject.py::test_inject_v4_worker_gz_input_upgrades_with_encoding_marker`、`tests/services/test_agent_artifact_inject.py::test_inject_legacy_worker_keeps_cas_form_for_gz_input`；论证：同 key 覆盖者只能是 v4 合法 gzip（.gz staging key 仅 v4+ Worker 可写），content_hash 三处同为未压缩口径（Worker gunzip 后校验 / verify_remote_digest 解压流 / stage_agent_inputs 原始字节） |
| INV-1 | C3 | ✅ | `tests/workflows/test_output_validation_view.py::test_input_bytes_come_from_the_dispatch_frozen_cas_copy`（冻结 ref）、`tests/workflows/test_output_validation_view.py::test_cas_blob_missing_falls_back_to_the_job_dir`（blob 缺失）、`tests/services/test_agent_artifact_inject.py::test_inject_keeps_cas_form_for_inputs_without_row`（legacy 无行）、`tests/workflows/test_output_validation_view.py::test_input_refs_without_a_store_take_the_job_dir`（无 store） |
| INV-1 | E1 | ✅ | `tests/services/test_agent_artifact_inject.py::test_inject_keeps_cas_form_when_row_was_rewritten_after_dispatch`、`tests/services/test_agent_artifact_inject.py::test_inject_v4_worker_keeps_cas_form_for_rewritten_gz_row`、`tests/services/test_agent_artifact_inject.py::test_inject_dict_ref_with_rewritten_row_downgrades_to_cas` |
| INV-1 | E2 | ✅ | `tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_dict_form_falls_back_to_cas_on_digest_mismatch`、`tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_presigned_http_failure_falls_back_to_cas`、`tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_truncated_gzip_falls_back_to_cas` |
| INV-1 | E3 | ✅ | `tests/db/test_completion_view_inputs.py::test_legacy_channel_output_ref_registered_before_validation`、`tests/db/test_completion_view_inputs.py::test_colliding_undeclared_report_skips_the_frozen_input_slot` |
| INV-1 | E5 | ✅ | `tests/workflows/test_output_validation_view.py::test_duplicate_input_aliases_resolve_last_wins` |
| INV-1 | E6 | 🧱 | reclaim 三点闭环：staging 源的唯一安全删除点是 finish 提交之后（`discard_staging_refs`，codex #774 复审钉死）；requeue 后重 claim 用新鲜 manifest 重新判定身份；CAS blob 由 (job,node) ref 防 GC（job 存活期间不可回收）。三点各自封闭，无残余竞态面 |
| INV-1 | C7 | ✅ | `tests/db/test_completion_view_inputs.py::test_colliding_undeclared_report_skips_the_frozen_input_slot`（撞名上报）、`tests/db/test_completion_truncated_manifest.py::test_completion_truncated_manifest_judged_from_archive_view`（截断 manifest） |
| INV-1 | C8 | ✅ | `tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_fallback_failure_carries_both_segments`（两段皆败归因）、`tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_fallback_cas_bytes_are_digest_verified`（CAS 假字节自验）、`tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_corrupt_gzip_header_falls_back_to_cas`（gzip 头坏）、`tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_truncated_gzip_falls_back_to_cas`（截断）、`tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_corrupt_deflate_body_falls_back_to_cas`（deflate 体坏——zlib.error 经归一化进回落族） |
| INV-2 | C1 | ✅ | `tests/workflows/test_output_validation_view.py::test_undeclared_files_never_enter_the_view`、`tests/db/test_completion_view_inputs.py::test_declared_input_never_backfills_expected_output`（归档/残留永不可达） |
| INV-2 | E4 | ✅ | `tests/workflows/test_output_validation_view.py::test_sibling_and_stale_fail_files_cannot_poison_this_node`、`tests/workflows/test_output_validation_view.py::test_double_review_outcomes_reflect_own_verdicts_only` |
| INV-2 | C6 | ✅ | `tests/workflows/test_output_validation_view.py::test_validator_created_undeclared_files_never_propagate`、`tests/workflows/test_output_validation_view.py::test_unsafe_declared_names_never_escape_the_view` |
| INV-2 | C8 | ✅ | `tests/services/test_result_validate_pool.py::test_broken_pool_rebuilds_and_recovers`；论证：每次校验的 scratch 目录由 TemporaryDirectory 唯一命名（mkdtemp 语义），并发双完成与池重建重试的视图互不可见 |
| INV-3 | C4 | ✅ | `tests/workflows/test_output_validation_view.py::test_private_input_copy_has_its_own_inode`、`tests/workflows/test_output_validation_view.py::test_reflink_unsupported_falls_back_to_full_copy` |
| INV-3 | C6 | ✅ | `tests/workflows/test_output_validation_view.py::test_validator_input_writes_cannot_reach_the_job_dir`、`tests/workflows/test_output_validation_view.py::test_validator_chmod_on_an_input_cannot_reach_the_job_dir`、`tests/workflows/test_output_validation_view.py::test_validator_replacing_an_input_fails_closed_without_polluting_the_job_dir` |
| INV-3 | C3 | ✅ | `tests/workflows/test_output_validation_view.py::test_cas_sourced_input_is_still_a_private_copy`（写隔离覆盖 CAS 来源字节） |
| INV-3 | C8 | ✅ | `tests/workflows/test_output_validation_view_files.py::test_reflink_probe_residue_stays_out_of_the_view`（unlink 失败探测残留不进视图） |
| INV-4 | C6 | ✅ | `tests/workflows/test_output_validation_view.py::test_validator_output_writes_reach_the_run_view_bytes`（in-place）、`tests/workflows/test_output_validation_view.py::test_validator_output_replace_family_reconciles_into_the_run_view`（replace 族）、`tests/workflows/test_output_validation_view.py::test_validator_deleted_output_propagates_the_deletion`（delete）、`tests/workflows/test_output_validation_view.py::test_validator_output_mutations_reconcile_even_when_the_verdict_fails`（失败 verdict 也 reconcile） |
| INV-4 | C4 | ✅ | `tests/workflows/test_output_validation_view_files.py::test_modified_copied_output_still_syncs_back` |
| INV-4 | E4 | ✅ | `tests/db/test_completion_generation_gates.py::test_completion_stale_finish_never_lands_bytes_or_rows`；论证：stale completion 的副作用只落私有 staging（代次闸拒绝提升），并发完成者各持各的视图 |
| INV-4 | C8 | 🧱 | 池崩溃整任务重试的非幂等窗口：reconcile 只在视图 context 清洁退出臂执行，崩溃要留部分状态必须崩在 reconcile 内部（亚秒级）；视图只放声明名，非幂等 validator 的重复施加面有界于其自身声明输出（clean-in-place 幂等设计）。成文于 `result_validate_pool.py` 的 `validate_skill_commit_outputs` docstring |
| INV-5 | E3 | ✅ | `tests/db/test_completion_view_inputs.py::test_legacy_channel_output_ref_registered_before_validation`（校验前登记 = GC 防护） |
| INV-5 | C7 | ✅ | `tests/db/test_completion_view_inputs.py::test_colliding_undeclared_report_skips_the_frozen_input_slot`（撞名守卫） |
| INV-5 | C6 | ✅ | `tests/db/test_completion_view_inputs.py::test_rmw_colliding_name_registers_normally`；论证：RMW 名（同名 declared input+output）在视图中取产物字节（output_rels 排除 input 臂），校验从不为该名读冻结 input blob——槽位在校验前被覆盖无孤儿窗口，撞槽无校验面 |
| INV-6 | C6 | ✅ | `tests/workflows/test_output_validation_view_files.py::test_same_size_rewrite_with_restored_mtime_still_syncs_back`（同尺寸改写 + utime 回拨的伪造被内容 hash 看穿） |
| INV-6 | C4 | ✅ | `tests/workflows/test_output_validation_view_files.py::test_unmodified_copied_outputs_skip_sync_back`（真未修改仍跳过，F5 优化保留） |
| INV-7 | E5 | ✅ | `tests/workflows/test_output_validation_view.py::test_duplicate_input_aliases_resolve_last_wins`（两别名冻结不同 digest 时视图取最后一个 = Worker 实际消费） |
| INV-7 | C3 | ✅ | `tests/workflows/test_output_validation_view.py::test_duplicate_declarations_place_once`（归一化去重、快照不误判，#868） |
| INV-7 | C1 | 🧱 | dispatch 侧（`stage_agent_inputs`）刻意不去重：consumer 侧 last-wins 规则是唯一事实源，dispatch 去重会引入第二个决策点（EXEC-INPUT-IDENTITY-001 statement 钉死）；Worker 顺序物化天然实现 last-wins，重复下载是病态配置的固有代价 |
| INV-8 | C8 | ✅ | `tests/workflows/test_output_validation_view.py::test_view_construction_failure_fails_closed`、`tests/workflows/test_output_validation_view.py::test_placement_failure_fails_closed`、`tests/workflows/test_output_validation_view.py::test_skill_missing_legacy_script_fails_closed`（构造/placement/契约缺失 fail-closed） |
| INV-8 | C3 | ✅ | `tests/workflows/test_output_validation_view.py::test_missing_declared_entries_are_absent_not_errors`（缺源 fail-open） |
| INV-8 | C8 | ✅ | `tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_presigned_failure_and_cas_missing_message`（两段式归因）、`tests/workers/test_artifact_object_channel.py::test_gzip_decode_surface_normalizes_to_runtime_error`（异常分类学归一化单点：gzip 三层错误面 → RuntimeError，下载层永不泄漏 zlib.error） |
| INV-8 | C7 | ✅ | `tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_dict_form_verifies_sha256`、`tests/workers/test_artifact_object_channel.py::test_download_input_artifacts_gzip_form_detects_tamper`（脏字节拒绝） |
| INV-8 | C7 | ✅ | `tests/db/test_completion_truncated_manifest.py::test_completion_truncated_manifest_judged_from_archive_view`、`tests/db/test_completion_truncated_manifest.py::test_completion_truncated_manifest_missing_output_still_fails`（截断 manifest 从视图判定） |
| INV-8 | C7 | ✅ | `tests/db/test_completion_generation_gates.py::test_completion_ref_channel_wins_over_duplicate_archive_member`（双通道同名 ref 字节获胜） |
| INV-8 | C8 | ✅ | `tests/db/test_completion_generation_gates.py::test_finish_gate_promotion_failure_converts_to_failed_not_wedge`（闸内提升失败转 failed 不卡死） |

## 5. 使用规程

1. **新发现先定位格子再修**：先回答「它落在哪个（不变量 × 轴）」——
   能定位说明网格有解释力，修完在该格补 ✅ 钉；定位不了说明轴/不变
   量不全，先扩网格（§2/§3）再修。
2. **修完补钉是交付的一部分**：没有钉进格子的修复视为未完成。
3. **结构性不可达必须写论证进格子**（🧱），论证要点名「为什么物理
   上不可能」，不接受「应该没事」。
4. **一致性检查**（`tests/scripts/test_artifact_identity_grid.py`）强
   制：✅ 格点名的测试必须真实存在（文件 + `::符号` AST 解析），🧱
   格论证必须非空，⬜ 与任何第四态拒绝，轴 token 必须落在图例内。
   网格即活文档——测试改名/删除会立刻红。
5. **枚举纪律**：凡格子涉及底层库异常面，钉之前必须先列该库的完整
   错误清单——gzip 样例：三层错误面（`BadGzipFile` 头/容器，OSError
   族；`EOFError` 截断；`zlib.error` deflate 体损坏，直接继承
   Exception）各自落在哪个 catch 集合里逐一确认，归一化单点收在下载
   层（`worker/artifact/gzip.py::copy_stream`），调用方分类学归零。
   漏一层就是 #876 codex P2 第五轮（zlib.error 绕过回落）的重演。

## 6. 战绩：历史发现 → 格子归宿

| 发现 | 出处 | 格子 | 修复 |
|---|---|---|---|
| 兄弟节点陈旧 fail 文件冤判 pass run | #757 本体 | INV-2 × E4 | #855 声明校验视图 |
| 跨文件对账 validator 失去 inputs 数据面 | #828/#830 | INV-2 × C1 | 热修 #833（inputs 链回），合并后下沉视图族 |
| 校验对覆盖后的 job_dir 现场字节 | codex #833 复审 P1 | INV-1 × E1/C3 | dispatch 冻结 CAS 字节优先 |
| 非规范拼写（`./out.json`）绕过同名排除 | codex #833 复审 P2 | INV-7 × C3 | 归一化比较（safe_relative 折叠） |
| 重复声明 inputs 快照误判被改 | #855 codex R4 → #868 | INV-7 × C3 | 归一化去重（后升级 keep-last） |
| claim 按当下行升级覆盖冻结身份 | #876 codex P1 第一轮 | INV-1 × E1 | 签发点 content_hash 比对守卫 |
| presigned URL 指向可变 authority key | #876 codex P1 第二轮 | INV-1 × E2 | 消费点 digest 自验 + CAS 回落 |
| 去重 keep-first 与 Worker 消费顺序错位 | #876 codex P2 第三轮 | INV-7 × E5 | keep-last |
| dict ref 绕过 claim 守卫被改写身份 | 对抗 B 员 P2-latent（F1） | INV-1 × E1 | dict 臂纳入守卫、失配降级 CAS 形态 |
| 传输/解码失败绕过回落裸奔出局 | 对抗 B 员 P2-（F2） | INV-1 × C8 | 全失败面回落、两段式归因 |
| reflink 探测残留进 validator 视野 | 对抗 B 员 P3（F3） | INV-3 × C8 | 探测点挪到视图外（同 st_dev） |
| 未修改拷贝输出白付写放大 | 对抗 B 员 P3（F5） | INV-6 × C4 | 未修改跳过 sync_back |
| 池崩溃整任务重试的非幂等窗口 | 对抗 B 员 P3（F4） | INV-4 × C8 | 🧱 成文（结构性有界论证） |
| add_ref 撞名把冻结 input 顶成孤儿 | 对抗 A 员 P2（A1） | INV-5 × C7 | 撞名守卫（终态见下行） |
| 校验窗口零引用 blob 被 GC 后 500 | #876 codex P2-1 第四轮 | INV-5 × E3 | 校验前登记复位 + 撞名守卫终态 |
| mtime+size 未修改判定可伪造 | #876 codex P2-2 第四轮 | INV-6 × C6 | 内容 sha256 判定 |
| gzip deflate 体损坏（zlib.error）绕过回落 | #876 codex P2 第五轮 | INV-1 × C8 | 下载层归一化（`copy_stream` 单点，三层错误面 → RuntimeError）+ 枚举纪律入规程 |
