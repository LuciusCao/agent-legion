# Studio 本地文件编辑契约

对应 STUDIO-MCP-FILES-001。传输方式不改变权限、内容归属、保存事务或发布权限。

## 内容与操作模型

| 对象 | 编辑读取的权威来源 | 保存语义 | 省略文件 | 限制 |
| --- | --- | --- | --- | --- |
| node code | backend 当前 draft（非空优先）或 published 文本 | 整段草稿替换 | 不适用 | 后端代码限制；本地原始输入 16 MiB |
| skill | 一次解析得到的 HEAD/tag commit；按 blob OID 读取 | 一次增量写入并 commit/tag | 保留 | 每次保存 1–100 文件，不限制仓库总文件数 |
| shared materials | 同一目录锁内的完整树，包含原始 map.json | 全量校验、暂存后原子替换 | 删除 | 全量状态最多 100 文件 |

普通展示读取保留原来的筛选、截断行为，不能充当编辑源。
skill 编辑读取与保存共用 workspace ownership 检查；group skill 仅可使用受限展示。
Git 编辑快照不读取 working tree：ignored、untracked、staged 和未提交修改均不进入 API。
返回的 commit 与内容来自同一个不可变树，即便读取期间 HEAD 被外部进程移动也不混代。
本地 dirty 状态继续由原保存端拒绝；读取成功不承诺无条件保存成功（权限、tag、HEAD、共享映射可能变化）。

skill 与 shared 只共享 UTF-8 文件编码校验，不共享文件集合枚举。
skill 导出整个提交中的普通 blob，不按扩展名或显示白名单省略；symlink、gitlink、非法 UTF-8、不可写路径或超预算成员导致整个导出失败。
shared 全量读取遇到软硬链接、特殊文件、非法路径、无效 map 或不可编码成员同样整体失败，避免省略变成删除。

## 预算与提交边界

skill HEAD/tag 共用预检：单文件最多 128 KiB；总和为内容字节 + UTF-8 路径字节 + 每文件 128 字节元数据额度，最多 16 MiB。读取 blob 前先校验整棵树的模式、路径和尺寸。树目录元数据也受 16 MiB 限制。
MCP JSON 导出/导入最多 96 MiB，容纳控制字符六倍转义；原始文件和解码后的提交批次仍分别限制在 16 MiB。限制独立，不能互相代替。
一个 101 文件 skill 可以完整导出；本地选择不超过 100 个修改项再保存。不能直接将整个大快照当作提交，也不自动拆成多个有部分成功风险的 commit。
shared 必须保留全部未修改项，skill 必须排除 mapped shared copies，修改共享权威源后走既有同步流程。
不引入隐含删除、自动增量比较、CAS 或发布行为；既有 tag 冲突、dirty 检查和失败回滚继续生效。

## 测试矩阵

矩阵按语义轴做参数组合，不依赖评审评论数量。新增内容源或保存入口必须补对应组合。

| 维度 | 必测组合/断言 | 自动化证据 |
| --- | --- | --- |
| 内容权威 | HEAD/tag × ignored/untracked/modified/staged/deleted/symlink 本地状态；只返回提交字节 | `tests/services/test_skill_snapshot_contract.py::test_local_state_never_changes_committed_edit_snapshot` |
| 版本一致性 | HEAD/tag 指向不同提交；读取中 HEAD 移动仍返回原提交全部字节 | 同文件 `test_tag_and_head_read_distinct_commits`、`test_snapshot_stays_on_resolved_commit_if_head_moves` |
| 缺失来源 | HEAD/tag × 缺目录/无 commit/嵌套非仓库目录；404，绝不回落父仓库 | 同文件 `test_unavailable_commit_never_falls_back_to_parent_repository` |
| 仓库规模 | HEAD/tag × 99/100/101 文件；全部读取，无截断 | 同文件 `test_repository_size_is_not_a_save_batch_limit` |
| 不支持成员 | HEAD/tag × 非 UTF-8/超大 blob/symlink/gitlink；整体拒绝 | 同文件 `test_unsupported_committed_members_reject_entire_snapshot` |
| 字节与路径 | HEAD/tag × 单文件上限前/上限；Unicode/tab/newline 路径、可执行文件、NUL 字节 | 同文件 `test_file_byte_boundary_and_unusual_paths_round_trip` |
| 路径上限 | HEAD/tag × 512/513 字符，读取遵循写入路径上限 | 同文件 `test_git_snapshot_path_matches_save_path_limit` |
| 总预算 | HEAD/tag × 预算前/恰好预算/超预算；读取任何 blob 前完成总量预检 | 同文件 `test_snapshot_total_budget_boundary`、`test_total_budget_rejects_before_reading_any_blob` |
| 权限 | group HEAD/tag 编辑拒绝、展示不泄露；foreign/binding/missing × HEAD/tag × HTTP/MCP，鉴权在 Git 和暂存之前；既有 membership 拒绝 | `tests/mcp_server/test_edit_snapshot_integration.py::test_group_skill_display_does_not_authorize_edit_export`、`test_edit_export_authorization_precedes_git_and_staging`、`tests/routes/test_studio_agent_skill_tools.py` |
| skill 往返 | HEAD/tag 大仓库导出；超批次保存无副作用；选择单文件保存，其他内容及 ignored 本地文件不变 | `tests/mcp_server/test_edit_snapshot_integration.py::test_large_skill_snapshot_selected_save_preserves_other_files` |
| 导出原子性 | HEAD/tag × symlink/gitlink/非 UTF-8，经 HTTP/MCP 拒绝，不产生导出文件 | 同文件 `test_invalid_git_snapshot_never_creates_export` |
| shared 往返 | 原始 map、CRLF、未知扩展名/无扩展名；最大合法全量快照六倍转义，hash 不变 | 同文件 `test_shared_full_snapshot_preserves_map_and_every_writable_file`、`test_maximum_shared_snapshot_round_trip_with_worst_case_json_escaping` |
| shared 集合安全 | 软硬链接/FIFO/缺 map/非法路径/101 文件整体拒绝 | `tests/services/test_skill_edit_snapshot.py` |
| 本地通道 | workspace/目录/文件软硬链接与 FIFO、路径越界、鉴权先于读、混合坏批次不调用保存、拒绝覆盖、raw/JSON 独立预算 | `tests/mcp_server/test_local_files.py` |
| node 往返 | 大源码、转义/换行、三行修改、重复保存，真实后端 hash 一致 | `tests/mcp_server/test_local_files_integration.py` |
| 保存失败 | tag 冲突、dirty/untracked 覆盖拒绝、合同失败回滚、共享同步原有契约 | `tests/services/test_skill_editing.py`、`tests/routes/test_studio_agent_skill_tools.py` |

## Quality Impact

新增 Git 快照模块统一 HEAD/tag，不扩大读取权限或保存批次上限。
先用失败测试复现已有漏洞，再执行上述服务单元与真实 HTTP/MCP/PostgreSQL 测试；本地 affected gate 与 push smoke 约束回归，PR `quality-gate` 是合并凭证。
新增测试不能仅断言调用成功：必须检查完整文件集合、精确字节和失败前后的权威状态。
