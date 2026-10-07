# 备份与恢复 runbook（PostgreSQL / 实例对象存储 / vault 主密钥 / skill 仓库）

一个 Agent Legion 实例的持久状态分散在下表各处。§0 是**权威清单**：每一项的位置、
备份手段、恢复手段、恢复后核验都以它为准，后文各节只展开命令。PostgreSQL
本身的运维（版本、跨大版本 dump/restore、连接池）见
[postgresql-runbook.md](postgresql-runbook.md)；对象存储部署与槽位运维见
[materials-storage-deployment.md](materials-storage-deployment.md)；`data/`
目录各子目录的生命周期见 [data-layout.md](data-layout.md)。

## 0. 数据清单（权威）

恢复的统一原则：**先完整校验备份，再用备份整体替换目标**。不向现有数据做增量
合并：合并会把备份点之后写入的内容留在目标里，恢复后的数据库会读到「未来」的
字节或文件，且后续的孤儿 GC 都看不出来。被替换的现有数据先改名或另备一份留存，
§2.4 核验通过后再删。

| # | 数据 | 性质 | 位置（Docker stack / 原生 `make prod-up`） | 备份 | 恢复：校验 → 替换 | 恢复后核验 |
|---|---|---|---|---|---|---|
| D1 | PostgreSQL | 权威：全部业务与执行态、`materials` / `job_artifacts` 清单行、vault 密文 | 命名卷 `postgres-data`（`compose.local.yaml` 可改 bind；逻辑备份不依赖卷位置）/ `AGENT_LEGION_DATABASE_URL` 指向的本机实例 | §2.1 `pg_dump -Fc`，临时文件成功后改名 | §2.3 第 4 步：`pg_restore -f /dev/null` 解码整个归档 → 现库改名保留 → 建空库 → `--single-transaction --exit-on-error` 整体导入 | §2.4：infra 连接探测 `database`；能启动即说明迁移已前推 |
| D2 | 对象存储：本地 SeaweedFS（默认） | 权威：材料对象（bucket 根）、产物权威副本（`jobs/`）、Worker 直传暂存与 promote 回滚备份（`jobs-staging/`，含 `/.rollback/`） | 命名卷 `seaweedfs-data` 或 `compose.local.yaml` 的 bind 目录（§2 解析为 `OBJ_SRC`）/ 同左（原生形态也经 compose 起 `seaweedfs`） | 二选一：§2.2.2 卷级冷备份（tar，附对象数摘要，摘要只在冷备份时可用于比对），或 §2.2.1 S3 层快照 | 卷级：`tar tzf` 读完整个包 → 清空卷 → 解包；S3 层同 D4 | §2.4：对象数 / 总大小与备份摘要一致；DB 行 → 对象存在性核对 |
| D3 | 对象存储：本地 RustFS（逃生舱） | 同 D2 | 命名卷 `rustfs-data` 或 bind（服务 `rustfs`，profile `materials-local-rustfs`） | 同 D2（`OBJ_SVC=rustfs`） | 同 D2 | 同 D2 |
| D4 | 对象存储：外部 S3（或任一后端的 S3 层快照） | 同 D2；bucket 级配置（CORS、lifecycle 规则、versioning）不随对象走 | 外部服务（`deploy/.env` / 根 `.env` 的 `AGENT_LEGION_S3_*`） | §2.2.1：先存源 bucket 的对象清单 `objects.json`，再整个下载到**新的**时间戳目录（须在大小写敏感的文件系统上），与清单逐 key、逐大小比对，一致才写 `SHA256SUMS` 并成为正式备份；末尾为 `/` 且有内容的 key 或只差大小写的 key 落不了盘，备份判失败（本地后端改用卷级冷备份）。零字节的 `…/` 目录标记不承载数据，不备份 | §2.3 第 5 步：快照再按 `objects.json` 比对并按 `SHA256SUMS` 校验 → `aws s3 rm --recursive` 清空 bucket → `aws s3 cp --recursive` 全量上传 → bucket 新清单与下载回来的文件都与 `objects.json` 比对，并逐对象校验 sha256。**禁止**用 `aws s3 sync` 把备份同步回已有 bucket（见 §2.2.1）。CORS 由 `ensure-s3-bucket.py` 重建；lifecycle 规则（[materials-storage-deployment.md](materials-storage-deployment.md) §4）与 versioning 设置备份时自行记录、新 bucket 上手工重设 | 同 D2 |
| D5 | vault 主密钥 | 权威，**不在数据库里** | compose 解析出的 `KEY_FILE`（默认 `deploy/secrets/vault_master_key`）/ 根 `.env` 的 `AGENT_LEGION_VAULT_MASTER_KEY` 字面值或 `_FILE` 所指文件 | §1.1：复制到与 dump 分开的保管处 | §2.3 第 3 步：备份非空 → 与现有一致则不动；不一致则现有文件改名 `.pre-restore-<时间戳>` 留存后放回备份 | §2.4：容器内 `/run/secrets/vault_master_key` 与 `KEY_FILE` 的 sha256 一致；外部服务连接测试不返回 500 |
| D6 | 部署配置与凭据 | 权威配置（丢了可重建，但须同步改 PG 角色密码与对象存储凭据） | `deploy/.env`、`deploy/secrets/postgres_password`、`deploy/secrets/postgres_pgpass`、`deploy/compose.local.yaml`（均 gitignored）/ 根 `.env` | §1.1：文件复制 | §2.3 第 2 步：全新机器整份放回（先于一切）；原机回退**保留现有**并与备份 `diff`——PG 角色密码存在 PG 集群（卷）里而不在 dump 里，现有文件与现有集群匹配 | `docker compose "${F[@]}" config` 解析成功；`postgres` healthy |
| D7 | Host 数据根 `artifacts/` | 视实例：§1.2 判定 `artifact_refs` 非空时是 legacy CAS 唯一副本 | 卷 `host-data`（或 bind，`HD_SRC`）下 / `data/`（或 `AGENT_LEGION_DATA_DIR`）下 | §2.2.3（与 `jobs/` 同包） | §2.3 第 5 步：`tar tzf` → 删现有 `artifacts/` 与 `jobs/` → 解包 | §2.4：`artifact_refs` 引用的每个 blob 文件存在 |
| D8 | Host 数据根 `jobs/` | 视实例：§1.2 判定非空时含唯一副本；否则是可淘汰缓存 | 同 D7 | 判定非空：§2.2.3；判定为空：不备份 | 有备份：同 D7；无备份：**原机回退也必须清空现有 `jobs/`**——产物读取本地优先且不对照清单（`server/app/services/job_artifacts.py` 的 `read`），留着会读到备份点之后的文件 | §2.4 抽查产物下载 |
| D9 | Host 数据根 `logs/` | 非权威：按保留期轮转的执行日志；节点日志 `logs/jobs/<job_id>-<node_key>.log` 文件名固定，重跑时截断重写（`server/app/executors/_code_sandbox.py` 的 `O_TRUNC`） | 同 D7 | 有审计需求才归档：§2.2.3 末尾的 `BACKUP_VOL "$HD_SRC" logs-jobs logs/jobs` | §2.3 第 5 步。原机回退：有归档就 `tar tzf` 校验后用它整体替换 `logs/jobs/`，没有就清空 `logs/jobs/`——留着会让恢复后的 job 显示备份点之后的日志；代价是历史节点日志不再可看 | 不适用 |
| D10 | Host 数据根 `materials_cache/`、`agent_bundles/`、`packages/`、`videos/`、`artifacts/.staging/`、`native-prod.state`、`bin/`；`AGENT_LEGION_SKILLS_RUNS_DIR`（执行快照与锁的临时目录） | 可再生：内容寻址缓存 / 在途传输包 / 可重新导出的包 / 平台不再读写的目录 / CAS 写入暂存 / 原生 prod-up 运行态（PID 与端口）/ 原生 velites 副本（`ensure-velites.sh` 重建）/ 临时目录 | 同 D7（`SKILLS_RUNS_DIR` 默认在系统临时目录） | 不备份 | 不恢复；原机保留（缓存按内容寻址，不会读错；在途包由 reaper 清扫；运行态与副本由 prod-up 重写） | 不适用 |
| D11 | skill root（各 skill 的本地 Git 仓，含 `.git` 与 `_shared`） | 权威：DB `skill_lock` 只存 commit，内容无法从 DB 或对象存储重建 | `${AGENT_SKILLS_DIR:-../skills}` 解析出的宿主机目录（`SKILLS`）/ `~/.agents/skills` | §2.2.4 tar | §2.3 第 5 步：`tar tzf` → 现有目录改名 `.pre-restore-<时间戳>` → 解到新建的空目录 | §2.4：`skill_lock` 每个 commit `git cat-file -e` |
| D12 | velites 二进制 | 可再生：GitHub Release 产物 | `${VELITES_BIN:-../velites-bin/velites}` / `data/bin` 与 PATH（`ensure-velites.sh` 构建） | 不备份 | §2.3 第 6 步：按 `sha256.txt` 校验 tarball → 解压放到该路径 | `make prod-up docker` 的 `--wait` 通过（Worker healthy） |
| D13 | Worker runtime 配置 | 建议备份：丢了可重配 | `VELITES_CONFIG_DIR`（默认 `<仓库根>/velites-config/`，`models.json`）、`PI_CONFIG_DIR`（默认 `<仓库根>/pi-config/`）、`deploy/velites-provider.env`（0600） | §1.3 tar / 复制 | 全新机器：校验后整目录放回；原机保留 | `GET /api/agent-workers` 中 Worker 在线且上报 runtime |
| D14 | Worker 状态目录 | 建议备份：丢了可重新注册 | 卷 `worker-control`（`WC_SRC`；`worker.yaml`、`control_token`、`register_tokens/`）/ `data/agent-worker-service` | §1.3（§2.2.3 末尾的 `BACKUP_VOL`） | 全新机器：`RESTORE_INTO`（校验 → 清空卷 → 解包）；原机保留 | 同 D13；备份点之后才签发的 register token 不在恢复后的库里，需重新签发并注册 |
| D15 | Worker work root | 在途 / 缓存：execution dir、`upload_pending.json`、Worker 侧 `materials_cache` | 卷 `worker-data`（`WD_SRC`）/ `worker.yaml` 的 `work_root` | 不备份 | 原机回退：清空（其中是备份点之后的执行，不属于恢复后的库） | 不适用 |
| D16 | 远程 / standalone Worker 机器（`compose.worker.yaml` 等）的状态卷、work root 与 runtime 配置 | 同 D13–D15，在各自机器上 | 各 Worker 机器上的 `worker-control` / `worker-data` 卷与配置目录 | 同 D13–D14，在各机器上做 | 第 1 步停掉、恢复完成前不启动；原机回退在第 5 步「Worker 状态」清空其 work root；状态卷保留 | 同 D14 |
| D17 | Studio「Agent 助手」的 agent 本地会话：`~/.kimi-code/sessions`（或 `KIMI_CODE_HOME`）、`~/.kimi/sessions`（或 `KIMI_SHARE_DIR`）及其它 ACP agent 的 home | 非权威：会话记录与转录在 PostgreSQL（`studio_chat_sessions` 等），本地是 agent 自己的续接状态（`server/app/studio_chat/kimi_wire.py`、`kimi_task_store.py`） | Docker：Host 容器的可写层（`/root`，不在任何卷里）/ 原生：运行 Host 的用户 home | 不备份 | 不恢复。Docker 原机回退在第 1 步删除 Host 容器，可写层随之清掉；原生形态这是用户自己的 home（个人 CLI 也用），不清理，**接受后果**：续接备份点之前就存在的会话时，agent 可能带着备份点之后的上下文，需要干净上下文的新建会话 | 不适用 |
| D18 | `data/studio-mcp-files/`（MCP 工具的文件交换区，按 Host 进程 cwd 解析：`server/app/mcp_server/local_files.py`） | 非权威：agent 读写的临时交换文件 | Docker：`/app/data/studio-mcp-files`，在 Host 容器可写层，**不在** `host-data` 卷里 / 原生：`<仓库根>/data/studio-mcp-files` | 不备份 | 原机回退：Docker 同 D17 随删除 Host 容器清掉；原生删除该目录 | 不适用 |

**恢复顺序**（§2.3 按此编号）：停 Host、本机与远程 Worker → D6 部署配置 → D5 vault key → D1 PostgreSQL →
D2–D4 对象存储 → D7–D10 Host 数据根 → D11 skill root → D14–D16 Worker 状态 →
D12–D13 velites 与 runtime 配置 → 拉起 stack → §2.4 核验 → 恢复调度。部署配置
决定后面每一步解析出的路径与卷源，所以必须最先；数据库与各存储的恢复都在
Host 与全部 Worker 停止期间完成，之后才允许任何进程写入。

## 1. 备份口径

### 1.1 必须备份

- **PostgreSQL 逻辑备份**（D1）：`pg_dump -Fc` 全库。客户端版本与服务端同为
  PostgreSQL 17（Docker stack 用容器内自带的 `pg_dump` 即可）。
- **实例对象存储**（D2–D4）：材料 bucket（`AGENT_LEGION_S3_BUCKET`，默认
  `agent-legion`）的**全部**对象，包括 `jobs-staging/`：其中大多是 Worker 直传
  的暂存残留，但 key 含 `/.rollback/` 段的对象是 promote 恢复失败时幸存清单行
  所指旧字节的最后恢复源（见
  [materials-storage-deployment.md](materials-storage-deployment.md) §4），不能
  按前缀整体略过。
- **vault 主密钥**（D5）：备份 Host 进程**实际读取**的那把 key。Docker stack 是
  `deploy/secrets/vault_master_key`（或 `VAULT_MASTER_KEY_FILE` 覆盖的路径）；
  原生形态 Host 只从进程环境 / 根 `.env` 读 `AGENT_LEGION_VAULT_MASTER_KEY`
  （key 字面值）或 `AGENT_LEGION_VAULT_MASTER_KEY_FILE`（所指文件），默认不读
  `deploy/secrets/vault_master_key`（`server/app/services/vault.py` 的
  `resolve_master_key`），备份的是该变量的值或它指向的文件。**与数据库备份分开存放**（例如单独的密钥保管处）：dump +
  key 放在一起，等于把全部 secret 明文交给拿到备份的人；但两者都必须可恢复。
- **skill root**（D11，整个目录，含每个 skill 仓的 `.git` 历史与 workspace 下的
  `_shared`）：skill 只以 skill root 下的本地 in-place Git 仓存在（无注册表、无
  远程 clone 通道），仓缺失即 dispatch 报错；DB `skill_lock` 冻结的是 commit
  sha，pinned ref 的执行要求该 commit 仍在仓里，只备份工作树不够。位置：原生
  形态为 `~/.agents/skills`（`server/app/skills/skill_roots.py`）；Docker stack 为
  `AGENT_SKILLS_DIR` 解析出的宿主机目录，同样让 compose 解析：
  `docker compose "${F[@]}" config | grep -B3 'target: /root/.agents/skills'` 输出的
  `source:`（`F` 见下文 key 文件一段）。下文记作 `SKILLS`，备份命令见 §2.2.4。
- **部署配置与凭据**（D6）：`deploy/secrets/postgres_password`、
  `deploy/secrets/postgres_pgpass`、`deploy/.env`（S3 凭据等）与
  `deploy/compose.local.yaml`（存在时）；原生形态为根 `.env`（数据库 URL、S3
  凭据，可能还有 vault key 字面值——此时它按 vault key 的要求单独保管）。丢了
  可以重新生成，但要同步改 PostgreSQL 角色密码与对象存储 root 凭据，有备份更省事。

**Docker stack 的 key 文件实际路径**：compose 的 secret 来源是
`${VAULT_MASTER_KEY_FILE:-./secrets/vault_master_key}`（`deploy/compose.host.yaml`
顶层 `secrets.vault_master_key.file`），变量可来自 shell 环境或 `deploy/.env`，
相对路径以 compose 文件所在的 `deploy/` 为基准。不要自己拼路径，让 compose 解析：
`docker compose … config` 输出末尾顶层 `secrets:` 段里 `vault_master_key` 的
`file:` 即解析后的绝对路径。存在 `deploy/compose.local.yaml` 时要一并传入（prod-up
入口同样会叠加它，它可能改写 secret 来源）。下文记作 `KEY_FILE`：

```bash
F=(-f deploy/compose.host.yaml)
[ -f deploy/compose.local.yaml ] && F+=(-f deploy/compose.local.yaml)
docker compose "${F[@]}" config | grep -A3 '^  vault_master_key:'
KEY_FILE=<上面输出中 file: 后的绝对路径>; KEY_FILE="${KEY_FILE%/}"
```

### 1.2 视实例情况必须备份：只在本地的 legacy 产物

Host 数据根（Docker stack 默认为卷 `host-data`，挂在容器 `/var/lib/agent-legion`，实际来源按 §2 解析；
原生形态为 `data/` 或 `AGENT_LEGION_DATA_DIR`）里有两类内容可能是**唯一副本**，
无法从对象存储重新物化（D7、D8）：

- `jobs/` 下的 job 目录：产物读取先看本地 job_dir、再按 `job_artifacts` 清单行回退
  对象存储（`server/app/services/job_artifacts.py`）。清单行按节点产物登记，缺行
  的节点产物只在本地 job_dir：实例启用对象存储产物（schema v54）之前完成的历史
  节点，或从未配置 `AGENT_LEGION_S3_BUCKET` 的实例上的全部节点。升级后重跑过
  部分节点的 job 是**混合**的——重跑节点有行，未重跑的旧节点仍只在本地；补传
  reconciler（`job_artifact_maintenance.py` 的 `reupload_missing`）只扫最近 7 天
  内完成的节点，不会替更早的节点补行。
- `artifacts/`：legacy 本地 CAS，`artifact_refs` 表引用的 blob 只存在这里；Worker
  直传缺上传规格、直传失败或崩溃恢复重进时也会回落到这条旧通道写入（见
  [data-layout.md](data-layout.md) §1）。

用数据库判定本实例是否有这类数据（Docker stack 经
`docker compose "${F[@]}" exec -T postgres psql -U agent_legion -d agent_legion -c '<SQL>'` 执行）：

```sql
-- > 0：artifact_refs 引用的 blob 只在 artifacts/，必须备份 artifacts/
select count(*) from artifact_refs;
-- > 0：这些 job 至少有一个已完成节点没有任何清单行（含混合 job），
--      该节点产物（若有）只在本地 job 目录
select count(distinct r.job_id) from node_runs r
where r.status = 'completed' and not exists (
  select 1 from job_artifacts a
  where a.job_id = r.job_id and a.node_key = r.node_key);
-- 列出这些 job 的目录：storage_dir 相对数据根解析，为空时即 jobs/<id>
select j.id, j.storage_dir from jobs j
where exists (
  select 1 from node_runs r
  where r.job_id = j.id and r.status = 'completed' and not exists (
    select 1 from job_artifacts a
    where a.job_id = r.job_id and a.node_key = r.node_key));
```

第二条按节点判定，是保守上界：不声明产物的节点也会计入。它看不出「节点只
登记了部分声明产物」（个别产物上传失败且已超出上面 7 天补传窗口；声明产物
清单在 workflow 定义里，SQL 无从比对），也看不出「重跑节点上传失败、完成已超
过 7 天」：旧的清单行仍在，SQL 计 0，但新内容只在本地 job 目录。所以只有两条都为 0 时数据根才可按
§1.4 当作缓存；任一非 0、或对上面两种盲区有疑虑，就把 `artifacts/` 与**整个** `jobs/` 随数据库一起备份
（第三条只用于了解涉及范围，不要据此只挑部分目录），命令见 §2.2.3。

### 1.3 建议备份

- Worker 的 runtime 配置（D13）：`VELITES_CONFIG_DIR` 目录（`models.json`；compose
  默认 `../velites-config`，按 `deploy/` 解析即 `<仓库根>/velites-config/`）、
  `PI_CONFIG_DIR` 目录（默认 `<仓库根>/pi-config/`）与
  `deploy/velites-provider.env`（provider 凭据，0600）。它们都是 clone 不会带上的
  本机文件：`.gitignore` 登记了 `deploy/velites-provider.env`，但目录只登记了
  `deploy/velites-config/` 与 `deploy/pi-config/`，默认位置 `<仓库根>/velites-config/`、
  `<仓库根>/pi-config/` 并未被忽略，只是未跟踪（不要提交它们）。丢失可按
  [agent-worker-deployment.md](agent-worker-deployment.md) §2 重配。
  velites 二进制本身不用备份，恢复时从 GitHub Release 重取（§2.3 第 6 步）。
- Worker 状态卷 `worker-control`（D14：状态副本 `worker.yaml`、control token、
  register token）：丢失可按 [agent-worker-deployment.md](agent-worker-deployment.md)
  重新配置与注册，备份只为省去重配。打包命令见 §2.2.3 末尾（`BACKUP_VOL "$WC_SRC" worker-control`）。

### 1.4 不需要备份

在 §1.2 的判定结果为空的前提下，`data/jobs/` 下的本地 job / run 目录是可淘汰
缓存；`data/materials_cache/`、`data/agent_bundles/`、`data/packages/`、
`data/logs/`（日志按保留期轮转，有审计需求再自行归档）以及 Worker 的 work root
都是缓存或在途文件，丢失后按需从对象存储重新物化或自动重建（D8–D10、D15）；
Studio agent 本地会话与 `data/studio-mcp-files` 也不备份（D17、D18）。
「不需要备份」不等于恢复时可以不管：原机回退时 `jobs/`、`logs/jobs/`、本机与远程
Worker 的 work root、Host 容器可写层仍要清掉，见 §2.3 第 1、5 步。

### 1.5 一致性：数据库与对象存储的时间差

数据库与对象存储无法原子地同时快照。产物写入是「先写对象、后写清单行」，
所以按下面的顺序做：

- **先 dump 数据库、再复制对象存储**：dump 之后新增的对象只是多出的孤儿；
  但 dump 与复制之间被删除或被取代的对象（材料 TTL 回收、产物同名重登记后
  旧版本对象的清理）在复制时已不存在，恢复后表现为「行在、对象缺失」。
- 要求强一致时做**冷备份**：先停 Host 与 Worker（Docker stack：
  `docker compose "${F[@]}" stop host worker`；原生形态：
  `make prod-down`），再依次备份数据库与对象存储，完成后重新 `make prod-up`
  （或 `make prod-up docker`）。
- 热备份可以接受时，恢复后按 §2.4 核对，缺失对象影响的 job 重跑即可。

## 2. 备份与恢复步骤（Docker stack）

以下命令在 prod worktree 根目录执行，bash 与 zsh 均可。compose 文件不是默认文件名，`-f` 不可省；
命名卷的实际名称带 compose 项目名前缀（`agent-legion_`），以
`docker volume ls` 为准。宿主机前置条件：`python3`、`git`、`sha256sum` 或
`shasum`、AWS CLI v2（`aws`，S3 层快照与 §2.4 核对用；对 RustFS 若报 checksum 相关
错误，加 `export AWS_REQUEST_CHECKSUM_CALCULATION=when_required`），以及能拉取
`busybox` 镜像的 Docker。备份目录须是**绝对路径**（`docker run -v` 要求），S3 层
快照的备份目录还须在**大小写敏感**的文件系统上（Linux 常见文件系统即可；macOS 默认
APFS 不区分大小写，需另建区分大小写的 APFS 卷）。

**先解析数据的实际挂载源**：`agent-legion_seaweedfs-data` / `agent-legion_host-data`
等只是基础编排的默认形态。`deploy/compose.local.yaml`（gitignored，Makefile 与
prod-up 入口存在即自动并入）可以把既有数据目录 bind-mount 到对象存储服务的
`/data`、`host` 的 `/var/lib/agent-legion` 或 Worker 的状态目录，这时命名卷是空的
或未被使用，照抄卷名会归档空卷、恢复时写回错误位置。因此这台机器有
`compose.local.yaml` 时要把它与 `deploy/.env` 一起备份、全新机器上一起先放回。
**解析卷源之前先确认 `compose.local.yaml` 已就位，`F` 必须在它就位之后计算**（§1.1
的 `F` 定义在它不存在时只含基础文件；放回后要重新执行一次，否则解析出的仍是命名
卷）。下文的 `docker compose` 命令都用这个 `F`（不带它重建容器会退回命名卷），
`docker run -v` 的卷源一律用下面解析出的变量。本地对象存储后端按部署选择：
默认 SeaweedFS（`OBJ_SVC=seaweedfs`、`OBJ_PROFILE=materials-local`），rustfs
逃生舱（`OBJ_SVC=rustfs`、`OBJ_PROFILE=materials-local-rustfs`）；外部 S3 没有
本地卷，`OBJ_SRC` 一项跳过：

```bash
OBJ_SVC=seaweedfs; OBJ_PROFILE=materials-local   # rustfs：rustfs / materials-local-rustfs
docker compose "${F[@]}" --profile "$OBJ_PROFILE" config "$OBJ_SVC" host worker \
  | grep -B2 -A2 -E 'target: /(data|var/lib/agent-legion|var/lib/agent-legion-worker|var/lib/agent-legion-worker-control)$'
# type: bind 时 source 是宿主机绝对路径，直接用；
# type: volume 时 source 只是卷键，实际卷名不要手拼，读顶层 volumes 段该键下的 name:
docker compose "${F[@]}" --profile "$OBJ_PROFILE" config | sed -n '/^volumes:/,/^[a-z]/p'
OBJ_SRC=<对象存储 /data 的卷名或 bind 绝对路径>
HD_SRC=<host /var/lib/agent-legion 的卷名或 bind 绝对路径>
WD_SRC=<worker /var/lib/agent-legion-worker 的卷名或 bind 绝对路径>
WC_SRC=<worker /var/lib/agent-legion-worker-control 的卷名或 bind 绝对路径>
# 命名卷形态先确认卷存在：docker run -v 遇到不存在的卷名会静默新建空卷
for v in "$OBJ_SRC" "$HD_SRC" "$WD_SRC" "$WC_SRC"; do docker volume inspect "$v" >/dev/null || echo "卷不存在：$v" >&2; done
```

bind 形态跳过 `docker volume inspect`，改为确认该目录存在（`[ -d "$OBJ_SRC" ]`）；
外部 S3 没有 `OBJ_SRC`，从循环里去掉。
仅在容器已存在时（`docker compose "${F[@]}" ps -aq "$OBJ_SVC"` 有输出；全新机器
恢复前通常没有）可再用 `docker inspect -f '{{range .Mounts}}{{.Type}} {{.Name}} {{.Source}} -> {{.Destination}}{{println}}{{end}}' "$(docker compose "${F[@]}" ps -aq "$OBJ_SVC")"`
（`host`、`worker` 同理）交叉核对。`docker run -v "$OBJ_SRC":/data` 对卷名与绝对路径都适用。
下文 tar 打包与解包前都应已通过上面的存在性检查。唯一例外是全新机器恢复：命名卷
尚不存在，`docker volume inspect` 失败属预期，此时确认各变量与 config
输出的 `name:` 逐字一致后跳过该检查，由 `docker run -v` 按该名称新建（compose 之后
复用同名卷，可能警告该卷不是 compose 创建的，属预期）。

**公共函数**：§2.1–§2.4 与 §4 的全部命令块只用下面这几个函数表达「失败分支」「原子
发布」「收尾重启」「校验后整体替换」「留存现有」，不再各自手写——每种语义只有一处
实现，新增一段命令时也只许调用它们。新开 shell 时与 `F` / `KEY_FILE` / 卷源一起重新
执行这一块（`BK` 为备份目录绝对路径，与 §2.1 相同）：

```bash
BK=<备份目录，绝对路径（docker run -v 要求）>; mkdir -p "$BK"
# FAILED <说明>：失败分支的唯一出口——打印到 stderr 并返回非零，定时任务按退出码告警
FAILED() { echo "未完成：$*" >&2; return 1; }
# PUBLISH <临时路径> <最终路径>：生产步骤成功后把临时文件 / 目录原子改名为正式备份；
#   最终路径已存在则拒绝（重试不会截断上一份成功的备份），临时结果留在原处供检查
PUBLISH() { [ ! -e "$2" ] && mv "$1" "$2" && echo "备份完成：$2"; }
# BACKUP_VOL <卷名或 bind 绝对路径> <文件名前缀> [<卷内相对路径>…]：把卷（或其中给出的路径）
#   打包为 $BK/<前缀>-$TS.tar.gz，先写 .partial 再 PUBLISH；不给路径则打包整个卷（含隐藏文件）。
#   调用前先设 TS（同一轮备份的摘要等旁文件共用它）
BACKUP_VOL() {
  local src="$1" name="$2"; shift 2; : "${TS:?先执行 TS=\"\$(date +%Y%m%d%H%M%S)\"}"
  local tmp="$BK/.$name-$TS.partial" out="$BK/$name-$TS.tar.gz"
  [ $# -gt 0 ] || set -- .
  docker run --rm -v "$src":/src:ro -v "$BK":/backup busybox tar czf "/backup/.$name-$TS.partial" -C /src "$@" \
    && PUBLISH "$tmp" "$out" || FAILED "检查 $tmp（tar 失败，或目标 $out 已存在）"
}
# THEN_UP <服务…>：用 ; 接在一条命令链之后——无论链成败都重新拉起服务，并把链的退出码原样返回
#   （链失败、重启也失败时打印警告，退出码仍是链的；成功的重启不会把失败的备份变成 0）
THEN_UP() { local rc=$?; docker compose "${F[@]}" --profile "$OBJ_PROFILE" up -d "$@" || echo "警告：重新拉起 $* 失败" >&2; return "$rc"; }
# RESTORE_INTO <卷名或 bind 绝对路径> <tar.gz 绝对路径> [<卷内相对路径>…]：先 tar tzf 读完整个
#   gzip 流（截断、损坏在这里失败，目标原样不动）；通过后删除给出的卷内路径（不给则清空整个卷，
#   含隐藏文件——rm -rf /dst/* 删不掉它们），再解包。整体替换、不做增量合并（§0 原则）
RESTORE_INTO() {
  local dst="$1" a="$2"; shift 2
  docker run --rm -v "$dst":/dst -v "$(dirname "$a")":/backup:ro busybox sh -c '
    a="/backup/$1"; shift
    tar tzf "$a" >/dev/null || exit 2
    if [ $# -eq 0 ]; then find /dst -mindepth 1 -delete; else cd /dst && rm -rf "$@"; fi && tar xzf "$a" -C /dst || exit 3
  ' sh "$(basename "$a")" "$@"
  case $? in
    0) echo "已用 $a 整体替换 $dst${1:+ 下的 $*}" ;;
    2) FAILED "$a 校验失败（tar tzf 未能读完整个包），$dst 未改动" ;;
    *) FAILED "清空或解包 $a 失败，$dst 已被改动——修复原因后重跑这一条" ;;
  esac
}
# KEEP_ASIDE <路径>：现有文件 / 目录改名为 <路径>.pre-restore-<时间戳> 留存（不存在则无事；
#   留存名已存在则失败，不覆盖上一次的留存）
KEEP_ASIDE() { [ ! -e "$1" ] || { local p="$1.pre-restore-$(date +%Y%m%d%H%M%S)"; [ ! -e "$p" ] && mv "$1" "$p" && echo "现有 $1 留存为 $p"; }; }
```

函数在 bash 与 zsh 下通用（`local` 两者都支持；`THEN_UP` 入口处的 `$?` 是前一条命令链的
退出码）。busybox 内只用 `tar` / `find` / `rm`，不依赖宿主机的 tar 实现。

### 2.1 PostgreSQL 备份

先写唯一的临时文件（`mktemp`，权限 0600），`pg_dump` 成功后再 `PUBLISH` 为最终文件；
最终文件名带到秒，且已存在时拒绝覆盖——重试不会截断上一份成功的备份，失败只
留下以 `.` 开头的临时文件（可直接删除）。命令在 bash 与 zsh 下通用（用函数包装
`docker compose`，原因见 §2.3 第 4 步）：

```bash
C() { docker compose "${F[@]}" exec -T postgres "$@"; }
OUT="$BK/agent_legion-$(date +%Y%m%d%H%M%S).dump"
TMP="$(mktemp "$BK/.agent_legion-dump.XXXXXX")" \
  && C pg_dump -U agent_legion -d agent_legion -Fc > "$TMP" \
  && PUBLISH "$TMP" "$OUT" \
  || FAILED "检查临时文件 $TMP（pg_dump 失败，或目标 $OUT 已存在）"
```

原生形态用本机 PostgreSQL 17 客户端，同样先写临时文件：把上面的
`C pg_dump … > "$TMP"` 换成 `pg_dump -Fc -d "$AGENT_LEGION_DATABASE_URL" -f "$TMP"`。

### 2.2 对象存储备份

两种手段：S3 层快照（§2.2.1，热备份可用，外部 S3 只能用它）与本地后端的卷级
冷备份（§2.2.2）。两者都要求每次备份落到**新的**带时间戳位置——反复同步到同一个
备份目标只保留最新状态，配不上更早那份 dump。

#### 2.2.1 S3 层快照（任一后端）

先把源 bucket 的对象清单（`list-objects-v2`，含每个 key 的大小）存为 `objects.json`，
再把整个 bucket 下载到新的空目录，然后用 `CHK` 把目录里的文件集合与清单逐 key、
逐大小比对，一致才对每个文件算 sha256 写成 `SHA256SUMS`，再连同它复核一遍（`SHA256SUMS`
的路径集合必须与文件集合逐字相等，清单有对象时不能为空），才把 `.partial` 目录改名
为正式备份。这一步比对不能省：`aws s3 sync` 下载时有两类 key 落不了盘，却仍返回 0——
末尾为 `/` 的 key 被当作目录，只差大小写的 key 在不区分大小写的文件系统上互相覆盖。
这类不一致让备份失败，而不是让恢复时的「清空 bucket」变成不可逆丢失。产物 key 不以
`/` 结尾（产物名不含 `/`：`job_artifact_objects.py` 的 `valid_artifact_name`）；材料 key
为 `{workspace_id}/{hash}/{filename}`，而材料 API 目前**不拒绝**以 `/` 结尾的
`filename`（`MaterialPresignRequest` 只限长度），客户端提交 `size_bytes=0` 的这类名字
会留下一条 ready 材料行和一个零字节、以 `/` 结尾的 key。所以零字节的 `…/` key（无论
是控制台建目录留下的标记，还是这种材料）不走文件系统：`CHK` 把它们从文件比对中剔除并
报出个数；它们的名字就在源清单 `objects.json` 里（与文件比对共用同一份清单，没有另外
的旁文件可被单独截断或改写），恢复时 `PUT_MARKERS` 据此按名重建为零字节对象，并在
清空 bucket 之后比对新清单与源清单的这一集合（§2.3 第 5 步），行 → 对象的核对因此仍能
通过。key 含换行时逐行格式无法无损表达，`MARKERS` 判失败、备份不成立，先处理掉该对象。其余落不了盘的 key 让 `CHK` 判失败：大小写
冲突换到大小写敏感的文件系统重做；末尾 `/` 且有内容的 key 不是平台数据，本地后端改用
§2.2.2 卷级冷备份，外部 S3 先查明来源并处理掉再备份。SeaweedFS 本身不保存这类 key 的内容：写入末尾为 `/` 的 key 时后端把它
存成零字节目录，内容被丢弃（实测 4.45），所以在 SeaweedFS 上它们都表现为目录标记。热备份期间对象仍在变化时，清单与下载之间的增删改同样会判失败，重试
或按 §1.5 停 Host 与 Worker 后再做。

本地后端从宿主机访问发布端口（SeaweedFS `http://127.0.0.1:8333`，rustfs
`http://127.0.0.1:9000`，`deploy/.env` 改过 `AGENT_LEGION_S3_BIND` 的换成该地址），
外部 S3 用其 endpoint（AWS 默认端点去掉 `--endpoint-url`）。宿主机没装 AWS CLI 时可以
用容器代替，其余命令不变：
`aws() { docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION -v "$BK":"$BK" amazon/aws-cli "$@"; }`，
并把 `EP` 换成容器可达的宿主机地址（Docker Desktop 为 `http://host.docker.internal:8333`）；
`-v "$BK":"$BK"` 让容器内外的备份路径一致，所以 `BK` 必须是绝对路径。下面定义的变量与函数在
§2.2.2、§2.3 第 5 步、§2.4 中复用：

```bash
B=<bucket>                        # AGENT_LEGION_S3_BUCKET，默认 agent-legion
EP=http://127.0.0.1:8333          # rustfs 为 :9000；外部 S3 为其 endpoint（AWS 默认端点则把下文的 --endpoint-url "$EP" 去掉）
export AWS_ACCESS_KEY_ID=<AGENT_LEGION_S3_ACCESS_KEY> \
       AWS_SECRET_ACCESS_KEY=<AGENT_LEGION_S3_SECRET_KEY> AWS_DEFAULT_REGION=us-east-1
S3() { aws --endpoint-url "$EP" s3 "$@"; }
LIST() { aws --endpoint-url "$EP" s3api list-objects-v2 --bucket "$B" --output json; }
# MARKERS <清单 JSON>：从清单（objects.json，与文件比对共用同一份、无旁文件）列出零字节、
# 以 / 结尾的 key（目录标记或以 / 结尾命名的空材料），排序后每行一个；它们落不了文件系统，
# 恢复时由 PUT_MARKERS 按名重建。key 含换行时逐行格式无法无损表达，直接判失败让人处理
MARKERS() { python3 -c '
import json,sys
t=open(sys.argv[1]).read().strip()
ks=sorted(o["Key"] for o in ((json.loads(t) if t else {}).get("Contents") or []) if o["Key"].endswith("/") and o["Size"] == 0)
bad=[k for k in ks if "\n" in k or "\r" in k]
if bad: print("零字节 / 结尾的 key 含换行，无法按行表达:", repr(bad[0]), file=sys.stderr); sys.exit(1)
print("\n".join(ks))' "$1"; }
# PUT_MARKERS <清单 JSON>：按 MARKERS 的输出重建零字节对象；清单解析失败或任一 put 失败即返回非零
PUT_MARKERS() { local ks k; ks="$(MARKERS "$1")" || return 1; while IFS= read -r k; do [ -z "$k" ] || aws --endpoint-url "$EP" s3api put-object --bucket "$B" --key "$k" --content-length 0 >/dev/null || return 1; done <<< "$ks"; }
# 优先 shasum：macOS 自带的 BSD sha256sum 不转义含换行的文件名，CHK 会因此判失败
command -v shasum >/dev/null && SHA=(shasum -a 256) || SHA=(sha256sum)
# CHK <目录> <清单 JSON> [SHA256SUMS]：目录里的文件集合与清单的 key 集合逐字相等、大小
# 逐个一致；给了 SHA256SUMS 时它的路径集合也必须与文件集合逐字相等（清单有对象时它不能
# 为空），且每个文件的 sha256 都对得上。全部满足才返回 0
CHK() { python3 - "$@" <<'PY'
import hashlib, json, os, re, sys
root, listing = sys.argv[1], sys.argv[2]
sums = sys.argv[3] if len(sys.argv) > 3 else None
with open(listing) as fh:
    text = fh.read().strip()
objs = (json.loads(text) if text else {}).get("Contents") or []
bad = [o["Key"] for o in objs if o["Key"].endswith("/") and o["Size"] > 0]
keys = {o["Key"]: o["Size"] for o in objs if not o["Key"].endswith("/")}
markers = len(objs) - len(keys) - len(bad)
files = {}
for d, _, names in os.walk(root):
    for n in names:
        p = os.path.join(d, n)
        files[os.path.relpath(p, root)] = os.path.getsize(p)
diff = sorted(set(keys) ^ set(files)) + sorted(k for k in keys.keys() & files.keys() if keys[k] != files[k])
if sums is not None:
    digests = {}
    with open(sums, encoding="utf-8", newline="\n") as fh:
        for line in fh.read().split("\n"):
            if not line:
                continue
            m = re.fullmatch(r"(\\?)([0-9a-f]{64}) [ *](.*)", line)
            if not m:
                diff.append("SHA256SUMS 格式错误: " + line[:80]); continue
            path = m.group(3)
            if m.group(1):
                path = re.sub(r"\\(.)", lambda e: "\n" if e.group(1) == "n" else e.group(1), path)
            digests[path[2:] if path.startswith("./") else path] = m.group(2)
    if keys and not digests:
        diff.append("SHA256SUMS 为空")
    diff += ["SHA256SUMS 路径不一致: " + k for k in sorted(set(digests) ^ set(files))]
    for k in sorted(digests.keys() & files.keys()):
        h = hashlib.sha256()
        with open(os.path.join(root, k), "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != digests[k]:
            diff.append("sha256 不符: " + k)
for k in bad + diff[:20]:
    print("不一致:", repr(k), file=sys.stderr)
print(f"清单对象 {len(keys)}（另有零字节目录标记 {markers} 个，不备份）、文件 {len(files)}、不一致 {len(bad) + len(diff)}", file=sys.stderr)
sys.exit(1 if bad or diff else 0)
PY
}
# BK 须在大小写敏感的文件系统上（见本节开头）
TS="$(date +%Y%m%d%H%M%S)"
OUT="$BK/s3-$B-$TS"; TMP="$BK/.s3-$B-$TS.partial"
mkdir -p "$TMP/objects" \
  && LIST > "$TMP/objects.json" \
  && MARKERS "$TMP/objects.json" >/dev/null \
  && S3 sync "s3://$B" "$TMP/objects" --only-show-errors \
  && CHK "$TMP/objects" "$TMP/objects.json" \
  && (cd "$TMP/objects" && find . -type f -exec "${SHA[@]}" {} +) > "$TMP/SHA256SUMS" \
  && CHK "$TMP/objects" "$TMP/objects.json" "$TMP/SHA256SUMS" \
  && PUBLISH "$TMP" "$OUT" \
  || FAILED "检查 $TMP（清单、下载或比对失败，或目标 $OUT 已存在）"
```

目标是新建的空目录，`aws s3 sync` 会下载每一个对象，这里用它只是为了递归下载；
**反方向不能这样用**：`aws s3 sync` 只在大小不同、源更新时间较新或目标不存在时
复制，备份之后被改写且大小相同的对象在目标里时间更新，会被跳过，`--delete` 也
只删多出来的 key、不覆盖这种对象。恢复因此一律走 §2.3 第 5 步的「清空 → 全量
上传 → 逐对象校验」。对象级元数据（如 Content-Type）不进本地目录，平台不依赖它：
材料下载的类型取自 `materials.content_type` 行（`server/app/services/material_cache.py`），
产物的响应头在签发时按名称写入（`server/app/services/external_artifact_access.py`）。
bucket 级配置也不在快照里：CORS 由恢复时的 `ensure-s3-bucket.py` 重建；配了
lifecycle 规则或 versioning 的，备份时自行记下（`aws s3api get-bucket-lifecycle-configuration`
/ `get-bucket-versioning`），恢复到新 bucket 后手工重设。

#### 2.2.2 本地后端卷级冷备份

先在后端运行时记下对象数与总大小摘要（恢复后用来核对，需要 §2.2.1 的 `S3` 函数），
再停对象存储容器，打包整个 `/data`（SeaweedFS 的 filer 元数据与 volume 文件、rustfs
的数据与元数据都在其中，停机打包才自洽）：

```bash
TS="$(date +%Y%m%d%H%M%S)"
SUM="$(S3 ls "s3://$B" --recursive --summarize)" \
  && printf '%s\n' "$SUM" | tail -2 > "$BK/$OBJ_SVC-data-$TS.summary" \
  && docker compose "${F[@]}" stop "$OBJ_SVC" \
  && BACKUP_VOL "$OBJ_SRC" "$OBJ_SVC-data"; THEN_UP "$OBJ_SVC"
```

与数据库备份同理：先写临时文件、成功后再改名，不覆盖已有备份。整条命令的退出码
是备份链的——`THEN_UP` 在摘要、停服、打包任一步失败后同样把服务拉起来，但不把
失败变成 0，定时任务可以直接按退出码告警。`.summary` 摘要
只对冷备份（Host 与 Worker 已按 §1.5 停止）有意义：热备份时摘要与打包之间仍可能
有写入，§2.4 的摘要比对会不一致，此时以 tar 校验与清单行 → 对象核对为准。对象存储服务挂在各自
profile 下，单独拉起时要带 `--profile`（或直接 `make prod-up docker`，由入口按
决策加 profile）。

#### 2.2.3 legacy 本地产物备份（§1.2 判定非空时）

Host 数据卷里的 `artifacts/` 与整个 `jobs/` 随数据库一起打包（`BACKUP_VOL` 先写
`.partial` 再 `PUBLISH`，与上面相同）：

```bash
TS="$(date +%Y%m%d%H%M%S)"
BACKUP_VOL "$HD_SRC" host-data artifacts jobs
```

热备份时这两处可能有正在写入的文件，强一致按 §1.5 先停 Host 与 Worker。原生
形态直接打包数据根下的 `artifacts/` 与 `jobs/`。某个目录不存在（例如从未写过 legacy CAS）时从参数里去掉它。
节点日志归档（D9，有审计需求时）与 Worker 状态卷（D14）同一写法：
`BACKUP_VOL "$HD_SRC" logs-jobs logs/jobs`、`BACKUP_VOL "$WC_SRC" worker-control`。

#### 2.2.4 skill root 备份

在宿主机上直接打包 `SKILLS`（§1.1；Docker 的挂载是宿主机目录，不需要进容器），
同样先写临时文件、成功后再改名：

```bash
SKILLS=<§1.1 解析出的 skill root>; SKILLS="${SKILLS%/}"   # 去掉末尾的 /，下文恢复时拼留存路径依赖这一点
OUT="$BK/skills-$(date +%Y%m%d%H%M%S).tar.gz"
TMP="$(mktemp "$BK/.skills-tar.XXXXXX")" \
  && tar czf "$TMP" -C "$SKILLS" . \
  && PUBLISH "$TMP" "$OUT" \
  || FAILED "检查临时文件 $TMP（tar 失败，或目标 $OUT 已存在）"
```

`-C "$SKILLS" .` 会带上 `.git` 等隐藏目录。打包期间不要
经平台编辑 skill 文件或 `_shared` 材料（会写仓库），否则可能打进半提交的 Git 状态。

### 2.3 恢复

恢复只能落到**同版本或更新版本**的代码上：启动时 `init_db` 只向前迁移
（`schema_migrations` 已记录到当前 `SCHEMA_VERSION` 即 no-op），不存在降级
路径——把新版本 dump 恢复给旧代码属于不受支持的形态。步骤顺序即 §0 的恢复顺序；
每一项都是「先校验备份，再整体替换」，被替换的现有数据留存到 §2.4 通过。

1. 停 Host 与 Worker，避免恢复期间有写入：
   `docker compose "${F[@]}" stop host worker`（原生形态 `make prod-down`）。远程 /
   standalone Worker（D16）也在各自机器上停掉（`docker compose -f deploy/compose.worker.yaml stop`
   等），恢复完成前不要再启动（它们的 work root 在第 5 步「Worker 状态」清空）。Docker stack 原机回退再删除 Host 容器：`docker compose "${F[@]}" rm -f host`
   ——它的可写层里有 agent 本地会话与 `data/studio-mcp-files`（D17、D18），都是备份点
   之后的状态，第 6 步拉起 stack 时会重新创建容器。
2. 部署配置（D6）。全新机器先放回：clean checkout 里没有 gitignored 的 `deploy/.env` 与
   `deploy/secrets/`，而 `postgres` 服务经 `POSTGRES_PASSWORD_FILE` 挂载
   compose secret `postgres_password`，文件缺失时容器起不来。把 §1.1 备份的
   `deploy/.env`、`deploy/secrets/postgres_password`、`deploy/secrets/postgres_pgpass`
   放回原位（`chmod 600`；`deploy/.env` 若用 `POSTGRES_PASSWORD_FILE` /
   `POSTGRES_PGPASS_FILE` 改写了来源，放到改写后的路径）。`deploy/.env` 要先于
   下一步放回：它可能用 `VAULT_MASTER_KEY_FILE` 改写 key 路径。原机有
   `deploy/compose.local.yaml` 的同样先放回（并按它重建 bind-mount 的宿主机目录）；
   原生形态放回根 `.env`。原机回退**不替换**这些文件：PostgreSQL 角色密码存在
   集群数据（卷）里、不在 dump 里，现有文件与现有集群匹配；用 `diff` 与备份比对，
   有差异逐项确认来由。
   **放回之后重新执行 §1.1 的 `F` 定义与 `KEY_FILE` 解析**（第 1 步时它可能还不
   存在，旧 `F` 只含基础文件：用它解析卷源、拉起 postgres 会把数据恢复进命名卷，
   而第 6 步入口读到 `compose.local.yaml` 改挂 bind 目录，恢复后数据为空）；之后
   才按 §2 开头解析 `OBJ_SRC` / `HD_SRC` / `WD_SRC` / `WC_SRC`，第 3 步起的命令都用新 `F`。
3. 恢复 vault 主密钥（D5）：把备份的 key 放回 Host 实际读取的位置（Docker stack 为
   §1.1 用 `docker compose … config` 解析出的 `KEY_FILE`，默认即
   `deploy/secrets/vault_master_key`；原生形态见 §1.1）。现有 key 若与备份不同
   （例如备份之后换过 key），dump 里的密文只认备份的那把，所以要替换，但先把现有
   文件改名留存：

   ```bash
   KEY_BK=<备份的 key 文件>
   if [ ! -s "$KEY_BK" ]; then FAILED "备份的 key 为空或不存在，停止"
   elif [ -e "$KEY_FILE" ] && cmp -s "$KEY_BK" "$KEY_FILE"; then echo "现有 key 与备份一致"
   else KEEP_ASIDE "$KEY_FILE" && cp "$KEY_BK" "$KEY_FILE" && chmod 600 "$KEY_FILE" && echo "已放回备份 key" \
       || FAILED "检查 $KEY_FILE 及其 .pre-restore-* 留存"
   fi
   ```

   **不要**在
   缺 key 文件或部署凭据的状态下运行 `scripts/install-deps.sh` 或
   `scripts/init-worktree.sh`：二者在 key 文件缺失或为空时会生成一把新 key，新
   key 解不开备份里的任何密文。全新机器上这时再只拉起数据库：
   `docker compose "${F[@]}" up -d postgres`（后续 `exec` 需要
   容器在运行）。
4. 恢复 PostgreSQL（D1）：先预检 dump，再把现库**改名保留**（不要 drop），然后建空库、整事务导入。
   旧库保留期间新旧两份数据并存，PostgreSQL 数据卷所在磁盘需要约两倍库体积
   的空闲空间。以下命令在 bash 与 zsh 下都可直接执行（用函数而不是字符串变量
   包装 `docker compose`，zsh 不会对未加引号的变量分词），各步以 `&&` 串联，
   任何一步失败即停止：

   ```bash
   C() { docker compose "${F[@]}" exec -T postgres "$@"; }
   DUMP="$BK/agent_legion-<时间戳>.dump"
   PRE="agent_legion_pre_restore_$(date +%Y%m%d%H%M%S)"   # 留存库名带时间戳：再次恢复不会与上一次的留存相撞
   # 预检：把整个归档解码为 SQL 丢弃，能读完说明文件完整（--list 只读头部与目录）
   C pg_restore -f /dev/null < "$DUMP" \
     && C psql -U agent_legion -d postgres -v ON_ERROR_STOP=1 \
          -c "ALTER DATABASE agent_legion RENAME TO $PRE" \
     && C createdb -U agent_legion -O agent_legion agent_legion \
     && C pg_restore -U agent_legion -d agent_legion --no-owner \
          --exit-on-error --single-transaction < "$DUMP" \
     && echo "恢复完成，恢复前的库留存为 $PRE" \
     || FAILED "数据库未恢复：预检失败则现库未动；改名之后失败的，按下面两条命令回到恢复前状态（留存库 $PRE）"
   ```

   整条命令的退出码就是恢复链的：成功提示在链内，任何一步失败都返回非零，不要在它
   失败后继续恢复对象存储或启动 Host。预检失败时后续步骤都不会执行，现库原样不动。`pg_restore` 默认遇错继续、只在
   结尾报错数，`--exit-on-error --single-transaction` 让任何一条失败都整体回滚，
   不会留下半导入的库。改名之后的步骤失败时，用下面两条命令回到恢复前状态：

   ```bash
   C dropdb -U agent_legion agent_legion
   C psql -U agent_legion -d postgres -v ON_ERROR_STOP=1 \
     -c "ALTER DATABASE $PRE RENAME TO agent_legion"
   ```

   旧库保留到 §2.4 全部核对通过后再删除：`C dropdb -U agent_legion "$PRE"`（`C` 即上面
   定义的函数）。历次恢复留下的库都以 `agent_legion_pre_restore_` 开头，
   `C psql -U agent_legion -d postgres -Atc "select datname from pg_database where datname like 'agent_legion_pre_restore_%'"`
   列出后逐个确认再删。
   原生形态用本机客户端走同样四步：`pg_restore -f /dev/null "$DUMP"` 预检；连同一
   实例的 `postgres` 库执行 `ALTER DATABASE <库名> RENAME TO <库名>_pre_restore_<时间戳>` 与
   `CREATE DATABASE <库名> OWNER <角色>`（需 CREATEDB 权限，没有就用超级用户执行这
   两条）；再 `pg_restore -d "$AGENT_LEGION_DATABASE_URL" --no-owner --exit-on-error --single-transaction "$DUMP"`。
5. 恢复对象存储（D2–D4）、Host 数据根（D7–D10、D18）、skill root（D11）与 Worker 状态（D14–D16）。

   **对象存储·S3 层快照**（§2.2.1 的备份；外部 S3 只有这一条路）：
   1. 本地后端先拉起并等到 healthy（`F` 见 §1.1）：
      `docker compose "${F[@]}" --profile "$OBJ_PROFILE" up -d --wait "$OBJ_SVC"`
      （外部 S3 跳过）。不带 `--wait` 时紧接的建 bucket 可能连不上；
   2. 建 bucket 与浏览器直传 CORS（`aws s3` 不会建 bucket，目标 bucket 不存在时
      上传直接失败；CORS 配置也不随对象复制）。Docker stack 用一次性 Host 容器
      执行——bucket、endpoint、凭据由 compose 按 `deploy/.env` 注入（宿主机直接读
      `deploy/.env` 时 bucket 未显式写出会被当作「未配置」静默跳过）：
      `docker compose "${F[@]}" run --rm --no-deps host python scripts/ensure-s3-bucket.py`。
      Host 容器内 `AGENT_LEGION_S3_ENDPOINT` 默认是 `http://seaweedfs:8333`，rustfs
      部署须已在 `deploy/.env` 覆盖为 `http://rustfs:9000`。全新机器上先执行下文
      「skill root」一段把备份放回（`KEEP_ASIDE` 对不存在的目录无事）再执行这条：
      `run host` 会挂载 `${AGENT_SKILLS_DIR:-../skills}`，绑定源不存在时 Linux 上
      Docker 以 root 创建它，之后普通用户解包会 EACCES。只先 `mkdir -p "$SKILLS"` 也行，
      但放回时 `KEEP_ASIDE` 会把这个空目录留存为 `.pre-restore-*`，§2.4 通过后一并删除；
      原生形态：`UV_CACHE_DIR=.uv-cache uv run python scripts/ensure-s3-bucket.py .env`；
   3. 校验备份、清空 bucket、全量上传、核对 bucket 清单并下载回来逐对象校验（`B` /
      `EP` / `S3` / `LIST` / `CHK` / `SHA` / `BK` 同 §2.2.1）。现有 bucket 里的内容还可能
      需要时，先按 §2.2.1 另做一份快照：

      ```bash
      SRC=<备份目录>/s3-<bucket>-<时间戳>
      CHK "$SRC/objects" "$SRC/objects.json" "$SRC/SHA256SUMS" \
        && S3 rm "s3://$B" --recursive --only-show-errors \
        && S3 cp "$SRC/objects" "s3://$B" --recursive --only-show-errors \
        && PUT_MARKERS "$SRC/objects.json" \
        && V="$(mktemp -d "$BK/.s3-verify.XXXXXX")" \
        && LIST > "$V.json" \
        && S3 sync "s3://$B" "$V" --only-show-errors \
        && CHK "$V" "$V.json" && CHK "$V" "$SRC/objects.json" "$SRC/SHA256SUMS" \
        && [ "$(MARKERS "$V.json")" = "$(MARKERS "$SRC/objects.json")" ] \
        && rm -rf "$V" "$V.json" && echo "对象存储已按备份替换并逐对象核验" \
        || FAILED "快照校验失败时 bucket 未被改动；清空之后失败的，修复原因后重跑整段"
      ```

      第一项校验快照本身：文件集合与备份时的源清单 `objects.json` 逐 key、逐大小一致
      （快照目录被改动、或被拷到大小写不敏感的文件系统上丢了文件，都会在这里失败），
      `SHA256SUMS` 的路径集合与文件集合逐字一致、清单有对象时不为空，每个文件 sha256
      都对得上（清单被清空或删掉一行、文件被同大小改写，都会失败）；失败时 bucket 原样不动。`aws s3 cp --recursive`
      无条件上传每个文件，不做大小 / 时间比较；源清单 `objects.json` 里零字节、以 `/` 结尾
      的 key（落不了文件系统，见 §2.2.1）由 `PUT_MARKERS` 按名重建为零字节对象，恢复后再
      比对新清单与源清单的这一集合逐字相等，以 `/` 结尾命名的空材料行因此仍有对象可对。
      最后取 bucket 的新清单、把 bucket 下载到
      新的空目录：下载结果与新清单一致、与备份时的源清单一致（多出或缺少的 key 都会被
      发现，零字节目录标记除外），内容再逐对象对 `SHA256SUMS`。外部 S3 开了版本控制时，
      `rm` 只留下删除标记，旧版本仍占空间。

   **对象存储·卷级冷备份**（§2.2.2 的备份）：停对象存储服务，`RESTORE_INTO` 校验后清空
   整个卷再解包，`THEN_UP` 重新拉起（无论成败）。`RESTORE_INTO` **先完整校验归档再删**：
   `tar tzf` 读完整个 gzip 流，截断或损坏的包会在这里失败，现有卷原样不动。现有卷还有
   可能需要的数据时，先按 §2.2.2 再冷备一份当前卷，与数据库恢复保留旧库同理：

   ```bash
   docker compose "${F[@]}" stop "$OBJ_SVC" \
     && RESTORE_INTO "$OBJ_SRC" "$BK/<seaweedfs|rustfs>-data-<时间戳>.tar.gz"; THEN_UP "$OBJ_SVC"
   ```

   **Host 数据根**：有 §2.2.3 的 legacy 本地产物备份时放回 Host 数据卷，同一函数、只换
   参数：校验通过后删掉卷里现有的 `artifacts/` 与 `jobs/`，再解包。
   不清空的话，备份之后才写入的文件会留在原处，而产物读取优先看本地 job_dir，
   恢复后的数据库会读到新旧混合的内容；包损坏时也不会解到一半才失败。两个目录
   按备份时的状态整体替换（备份里没有 `artifacts/` 说明当时就没有 legacy CAS，
   删掉现有的同样正确）。现有卷里的这两个目录还可能有用时，先按 §2.2.3 再打一份：
   `RESTORE_INTO "$HD_SRC" "$BK/host-data-<时间戳>.tar.gz" artifacts jobs`。
   **§1.2 判定为空、没有这份备份时，原机回退同样要清空 `jobs/`**（它是缓存，可以删；
   不删就是上面说的新旧混合）：`docker run --rm -v "$HD_SRC":/dst busybox rm -rf /dst/jobs`
   （Host 启动时会重建该目录）。节点日志同理（D9）：文件名按 `<job_id>-<node_key>` 固定、
   重跑时截断重写，留着会让恢复后的 job 显示备份点之后的日志。有归档就先校验、再整体替换：
   `RESTORE_INTO "$HD_SRC" "$BK/logs-jobs-<时间戳>.tar.gz" logs/jobs`；
   没有归档就清空：`docker run --rm -v "$HD_SRC":/dst busybox rm -rf /dst/logs/jobs`。
   `materials_cache/`、`agent_bundles/`、`packages/`、`videos/` 等不动（§0 D10）。原生形态
   在数据根上同样操作，并删除 `<仓库根>/data/studio-mcp-files`（D18）。

   **skill root**：先校验包，再把现有目录整体改名留存，解到新建的空目录——不要解进
   非空目录，那样备份之后新增的文件、分支与对象会留下来。Docker stack 的 `SKILLS`
   须是 compose 解析出的同一挂载源（Host 已在第 1 步停止，重新启动时按路径挂载新目录）：

   ```bash
   SKILLS="${SKILLS%/}"   # 末尾带 / 时留存路径会落进目录内部
   A="$BK/skills-<时间戳>.tar.gz"
   tar tzf "$A" >/dev/null \
     && KEEP_ASIDE "$SKILLS" \
     && mkdir -p "$SKILLS" && tar xzf "$A" -C "$SKILLS" \
     && echo "skill root 已按备份替换" \
     || FAILED "检查 $A、$SKILLS 及其 .pre-restore-* 留存"
   ```

   **Worker 状态**：原机回退时清空 work root（D15，其中是备份点之后的执行；恢复后
   仍显示进行中的 job 在 §2.4 核对时按需重跑）：
   `docker run --rm -v "$WD_SRC":/w busybox find /w -mindepth 1 -delete`（原生形态清空
   Worker `worker.yaml` 中 `work_root` 指向的目录）；远程 / standalone Worker（D16）在
   各自机器上对其 `worker-data` 卷（或 `work_root`）做同样的清空；
   `worker-control`（D14）保留现有。全新机器有 §1.3 的 `worker-control` 备份时，
   `RESTORE_INTO "$WC_SRC" "$BK/worker-control-<时间戳>.tar.gz"` 放回（校验 → 清空整个卷 → 解包）。
6. 全新机器先备好 Worker 的 velites 二进制（D12）：它不在仓库、镜像与本 runbook 的备份
   里（`velites-bin/` 是 gitignored 目录），而 compose 把
   `${VELITES_BIN:-../velites-bin/velites}`（默认即 `<仓库根>/velites-bin/velites`）
   挂进 Worker。文件缺失时 Docker 会在该路径建一个空目录，容器照常创建，随后
   期望 runtime 守卫（`AGENT_WORKER_EXPECT_RUNTIMES`，默认 `velites`）探测不到
   velites，Worker 以退出码 2 退出，下面入口的 `--wait` 随之失败。仓库没有自动
   下载入口（`Makefile` 与 `scripts/stack-prod-up.sh` 不处理它，
   `scripts/ensure-velites.sh` 只为裸机形态从源码构建到 `data/bin`），按
   [agent-worker-deployment.md](agent-worker-deployment.md) §5「velites 二进制来源」
   从 GitHub Release（`velites-v*` tag）取 Linux 产物
   `velites-<ver>-<arch>-unknown-linux-gnu.tar.gz`：二进制在容器里运行，架构按
   Docker 引擎而不是宿主机操作系统，x86_64 取 `x86_64`，arm64（含 Apple silicon
   上的 Docker Desktop）取 `aarch64`；不要取 `aarch64-apple-darwin`，那是裸机
   macOS 用的。产物是 tarball：先对照同一 Release 附带的 `sha256.txt` 校验
   （`sha256sum -c --ignore-missing sha256.txt`，macOS 用
   `shasum -a 256 -c --ignore-missing sha256.txt`——清单列出全部平台的 tarball，不带
   `--ignore-missing` 会因其余产物未下载而返回非零），再
   解压，取出其中的 `velites-<ver>-<triple>/velites` 放到上述路径并 `chmod +x`；
   `deploy/.env` 用 `VELITES_BIN` 改过位置的放到改写后的路径。已经在缺文件的状态
   下启动过的，先删掉 Docker 建的空目录再放文件：Linux 上它由 daemon 以 root 创建，
   `rmdir` 可能需要 `sudo`。Worker 的 runtime 配置（D13：`VELITES_CONFIG_DIR` 的
   `models.json`、`PI_CONFIG_DIR`、`deploy/velites-provider.env`）同样不在仓库里，
   全新机器按 §1.3 的备份整目录放回，或按 agent-worker-deployment.md §2 重配。
   原机本就是零 runtime 形态（去掉 velites 挂载的 override、`deploy/.env` 里
   `AGENT_WORKER_EXPECT_RUNTIMES=` 置空）的，这两处已随第 2 步放回，无需二进制。
   然后 `make prod-up docker` 拉起整个 stack；低于当前版本的 dump 会在启动时自动
   迁移到当前 schema。入口等到 host 与 worker 都 healthy 才返回 0：Worker 的健康判定
   要求执行进程在跑，而它只在拿到有效注册 token 后才启动——第 5 步放回了
   `worker-control`（D14，含 `register_tokens/`）且恢复后的库里仍有该 Worker 的
   注册记录时会自动重新注册；没有 D14 备份（§1.3 只是建议备份）或 token 是备份点
   之后签发的，worker 保持 unhealthy、入口超时报错，但 Host 已正常起来，按
   [agent-worker-deployment.md](agent-worker-deployment.md) §4 重新签发 token 并导入即可，
   不必重跑恢复。注意该入口（`scripts/stack-prod-up.sh`）启动前**无条件**检查
   默认路径 `deploy/secrets/{postgres_password,postgres_pgpass,vault_master_key}`
   非空，不看 `POSTGRES_PASSWORD_FILE` / `POSTGRES_PGPASS_FILE` /
   `VAULT_MASTER_KEY_FILE` 覆盖（compose 实际挂载的仍是覆盖后的路径）。用了覆盖的
   部署二选一：在默认路径也放一份同内容文件（`chmod 600`，仅为通过预检，换 key 后
   要同步更新），或跳过入口直接执行它的等价命令（`F` 见 §1.1）：
   `docker compose "${F[@]}" $(./scripts/local-s3-decide.sh --compose-flags --default-endpoint http://seaweedfs:8333 deploy/.env) up -d --build --wait`。
   **拉起之前先看材料 TTL**：Host 一启动，后台 sweeper（`sweeper_enabled`）就开始跑，
   其中材料 TTL sweeper 把 `expires_at` 已过的就绪材料翻成 `expired`，宽限 10 分钟后
   无引用的随即连对象一起物理删除（`server/app/services/material_ttl.py`）。恢复间隔
   越长，越多材料会在启动后立刻被回收。启动前先数一下：
   `C psql -U agent_legion -d agent_legion -At -c "select count(*) from materials where status = 'ready' and expires_at is not null and expires_at <= now() + interval '1 hour'"`；
   非 0 且需要保留的，作为有意的数据改动延长它们的 `expires_at`（例如
   `update materials set expires_at = now() + interval '7 days' where status = 'ready' and expires_at <= now() + interval '1 hour'`）
   再启动。其余 sweeper（孤儿 GC、执行记录与 Studio 会话保留期清理）同样按恢复后的
   时间立即生效。
7. 后端每次启动都会把全部 workspace 调度重置为暂停（`server/app/main.py` 启动时
   调用 `reset_all_to_paused`），恢复后先完成 §2.4 的核对，再经控制台恢复调度。

### 2.4 恢复后核对

逐项对应 §0 的「恢复后核验」列；全部通过后再删除各处留存的 `pre_restore` 副本
（数据库 `$PRE`、`KEEP_ASIDE` 留下的 `.pre-restore-*` 文件与目录）。

- **数据库与对象存储可达**（D1–D4）：`GET /api/health` 的 `storage.reachable` 为真；
  admin 基础设施连接探测（`POST /api/admin/infra-connections/test`，`target` 分别取
  `database` / `storage`）显示数据库与对象存储均可达。
- **vault key 与库匹配**（D5）：Docker stack 先确认 Host 读到的就是放回的 key（两行
  输出一致；Linux 上没有 `shasum` 时换成 `sha256sum`）：

  ```bash
  docker compose "${F[@]}" exec -T host cat /run/secrets/vault_master_key | shasum -a 256
  shasum -a 256 < "$KEY_FILE"
  ```

  指纹一致只说明 Host 读到的是放回的那个文件，不说明它能解开库里的密文。再让 Host
  对**全部** `workspace_secrets` 与 `instance_secrets` 密文做一次解密核验（只计数、
  不输出明文；任一条解不开即返回非零，并打印是哪张表的哪一条）：

  ```bash
  docker compose "${F[@]}" exec -T host python - <<'PY'
  import os, psycopg
  from cryptography.fernet import Fernet, InvalidToken
  from server.app.services.vault import resolve_master_key
  f = Fernet(resolve_master_key().encode())
  ok = bad = 0
  with psycopg.connect(os.environ["AGENT_LEGION_DATABASE_URL"]) as conn:
      for table, cols in (("workspace_secrets", "workspace_id, name, ciphertext"),
                          ("instance_secrets", "'', name, ciphertext")):
          for scope, name, ciphertext in conn.execute(f"select {cols} from {table}"):
              try:
                  f.decrypt(ciphertext.encode()); ok += 1
              except InvalidToken:
                  bad += 1; print("无法解密:", table, scope, name)
  print(f"可解密 {ok} 条、不可解密 {bad} 条")
  raise SystemExit(1 if bad else 0)
  PY
  ```

  原生形态在 prod worktree 里用 `uv run python - <<'PY' … PY` 执行同一段（进程环境里
  有 `AGENT_LEGION_VAULT_MASTER_KEY*` 与 `AGENT_LEGION_DATABASE_URL`）。有外部服务连接
  的实例再对每个连接执行一次测试（admin 全局设置「外部服务连接」，或
  `POST /api/admin/connections/{key}/test`）：它会解析实例 vault 中的凭据并真的去连。
  key 对不上时该接口返回 HTTP 500（凭据解析在探测之前抛错），而不是 `ok: false`。
  只有解密核验为 0 条不可解密，才允许删除第 3 步与第 4 步的留存。
- **对象存储内容**（D2–D4）：S3 层恢复已在第 5 步逐对象核验；卷级恢复把对象数与总
  大小和备份时的摘要比对（两者一致才算通过）：
  `S3 ls "s3://$B" --recursive --summarize | tail -2 | diff - <备份目录>/<seaweedfs|rustfs>-data-<时间戳>.summary`。
  然后核对数据库里每条清单行与就绪材料指向的对象都存在（`C` 为 §2.3 第 4 步的函数，
  `B` / `LIST` / `BK` 同 §2.2.1）。在子 shell 里开 `pipefail`，管道任何一段失败都
  不会被当成「空结果」；先看打印的两个计数（库里有行而计数为 0 说明查询没取到数据），
  `comm` 的输出为空即通过，每一行都是「行在、对象缺失」的 key：

  ```bash
  ( set -o pipefail
    C psql -U agent_legion -d agent_legion -At -v ON_ERROR_STOP=1 \
        -c "select storage_key from job_artifacts union select storage_key from materials where status = 'ready'" \
      | LC_ALL=C sort -u > "$BK/.db-keys" \
    && LIST | python3 -c 'import json,sys; t=sys.stdin.read().strip(); [print(o["Key"]) for o in ((json.loads(t) if t else {}).get("Contents") or [])]' \
      | LC_ALL=C sort -u > "$BK/.obj-keys" \
    && echo "清单行 $(($(wc -l < "$BK/.db-keys")))、对象 $(($(wc -l < "$BK/.obj-keys")))" \
    && LC_ALL=C comm -23 "$BK/.db-keys" "$BK/.obj-keys" )
  ```

  冷备份恢复应当为空；热备份恢复可能列出 dump 与复制之间被删除或取代的对象，影响
  的 job 重跑即可。`scripts/gc-s3-jobs.py` 默认 dry-run（Docker stack 在 Host
  容器内执行：`docker compose "${F[@]}" exec host python scripts/gc-s3-jobs.py`），先只看报告——列出的
  是 dump 之后写入、清单里没有行的孤儿对象，确认无误后再加 `--apply`。「行在、
  对象缺失」的产物会让依赖它的下游节点停在等待中，job 详情页对应节点显示
  「输入恢复不全，建议重跑 <生产节点>」，按提示重跑生产节点即可。
- **legacy CAS**（D7，有 §2.2.3 备份时）：`artifact_refs` 引用的每个 blob 都在
  `artifacts/<hash 前 2 位>/<hash>`（`server/app/services/artifact_store.py` 的
  `open_blob`），输出为空即通过：

  ```bash
  C psql -U agent_legion -d agent_legion -At -v ON_ERROR_STOP=1 -c 'select distinct hash from artifact_refs' > "$BK/.cas-hashes" \
    && echo "引用的 blob $(($(wc -l < "$BK/.cas-hashes"))) 个" \
    && docker run --rm -v "$HD_SRC":/d:ro -v "$BK":/b:ro busybox sh -c \
      'while read -r h; do [ -f "/d/artifacts/$(echo "$h" | cut -c1-2)/$h" ] || echo "缺失 $h"; done < /b/.cas-hashes'
  ```

- **skill 锁定 commit 可物化**（D11）：DB `global_settings` 中 `skill_lock` 文档（JSON，
  `skills.<skill key>.refs.<ref> = <commit>`；早期 v1 条目是 `{repo, ref, commit}`，读取时
  自动升级，下面的脚本两种都认）记录的每个 commit 都必须存在于
  `SKILLS/<skill key>` 仓里（`server/app/skills/lock.py` 的仓位置约定）。逐个用
  `git cat-file -e` 核对（`latest` ref 跟随 HEAD、不进锁，仓存在即可）：

  ```bash
  ( set -o pipefail
    C psql -U agent_legion -d agent_legion -At -v ON_ERROR_STOP=1 \
        -c "select value from global_settings where key = 'skill_lock'" \
      | python3 -c 'import json,sys; t=sys.stdin.read().strip(); d=json.loads(t) if t else {}; [print(k, c) for k, s in (d.get("skills") or {}).items() for c in ((s.get("refs") or ({s["ref"]: s["commit"]} if s.get("ref") and s.get("commit") else {})).values())]' \
      > "$BK/.skill-lock" \
    && echo "锁定 commit $(($(wc -l < "$BK/.skill-lock"))) 个" \
    && while read -r key commit; do
         git -C "$SKILLS/$key" cat-file -e "$commit^{commit}" \
           && echo "ok $key $commit" || echo "缺失 $key $commit" >&2
       done < "$BK/.skill-lock" )
  ```

  有「缺失」即说明 skill 备份不是锁定时刻之后的版本，或仓的历史被改写过，需要
  找回含该 commit 的仓。**不要用 `make skills-lock` 做这项核对**：它会把每个已
  pin 的 ref 重新解析到仓里的当前 commit 并改写锁（`server/app/skills/lock.py`），
  等于用恢复后的仓覆盖锁定记录，掩盖缺失。
- **材料 TTL**：第 6 步启动前若没处理，现在查 `materials` 中 `status = 'expired'` 且
  `expires_at` 落在恢复间隔内的行，确认被回收的是否符合预期（对象已删的只能重新上传）。
- **Worker**（D12–D14、D16）：`GET /api/agent-workers` 中本机与远程 Worker 在线并上报期望的
  runtime。Worker 若是备份点之后才注册的（或其 register token 是之后签发的），
  恢复后的库不认识它，按 [agent-worker-deployment.md](agent-worker-deployment.md)
  在 workspace 设置里重新签发 token 并在 Worker 控制台重新注册。

## 3. 演练频率建议

- 每次涉及 schema 迁移的升级前，确认有一份当天的数据库备份（迁移幂等可重入，
  但只向前）。
- 每季度至少做一次完整恢复演练：在与生产隔离的环境（另一台机器，或开发机上
  用独立的 `COMPOSE_PROJECT_NAME` 起一套临时 stack，不要碰共享 / prod 库）用最近
  一份备份走完 §2.3 与 §2.4，两条路径都要走：clean checkout 上的全新机器恢复，以及
  在已有实例上先写入再回退（确认备份点之后的对象 / 行 / 文件 / skill 提交全部消失、
  留存副本仍在）。其中必须包含「用备份的 vault 主密钥通过一次外部服务连接测试」——只恢复
  了数据库、key 却对不上，是演练最常暴露的问题。
- 备份任务本身要有失败告警，并定期抽查备份可读：dump 用 `pg_restore --list`，
  S3 层快照用 §2.2.1 的 `CHK "$SRC/objects" "$SRC/objects.json" "$SRC/SHA256SUMS"`，
  tar 包用 `tar tzf`。
- 演练的耗时与数据量记录在内部运维记录里，不写进本仓库文档。

## 4. vault 主密钥丢失或泄露

### 4.1 后果（以 `server/app/services/vault.py` 为准）

vault 是单 key 的 Fernet 加密：没有多 key 并存、没有重新加密（rotate）命令，
也没有任何从密文找回明文的途径。key 丢失（或被换成另一把 key）后：

- **workspace secret 与节点配置里的 secret 字段**（`workspace_secrets`，节点配置
  只存 `{"secret_ref": ...}`）：解析时抛 `vault secret '<name>' cannot be decrypted
  with the configured master key`；完全未配置 key 时抛 `Vault master key is not
  configured`。引用 secret 的节点在派发时以配置错误失败，intake 冻结的
  `secret_ref` 同样解析失败。
- **外部服务连接凭据**（`instance_secrets`）：依赖该连接的节点全部失败，连接测试
  接口返回 HTTP 500。
- **连接 token 缓存**（`connection_tokens`）：解不开时视作过期并重新换取，本身能
  自愈——但换取要用上一条的凭据，所以凭据重录之前同样失败。
- **日志脱敏退化**：job 日志在读取时用该 workspace 的全部 vault 明文做替换脱敏
  （`collect_vault_plaintexts`），这一步是全有或全无的——该 workspace 只要还有
  **任意一个** secret 解不开，整个 workspace 的 vault 脱敏都静默关闭（不影响读
  日志），此前写入日志的 secret 值会以明文显示。只重录其中几个并不能恢复脱敏，
  必须让该 workspace 的每个 secret 都能被新 key 解开（重写或删除，见 §4.2 第 3
  步的完成核对）。实例级的外部服务连接凭据（`instance_secrets`）本来就不参与
  日志脱敏。
- 设置页对 secret 字段只显示「已设置」标记，它只看引用是否存在，**key 丢失后仍
  显示已设置**，不能据此判断 secret 是否可用。

服务本身照常启动，其余不涉及 secret 的功能不受影响。

### 4.2 处置流程

以下命令沿用 §1.1 的 `F` / `KEY_FILE` 与 §2「公共函数」块（`FAILED`），新开 shell 先执行它们。

1. **先找 key，不要急着生成新 key**。Docker stack 上**第一件事**是看 Host 容器是否
   还在运行：compose secret 是单文件 bind mount，宿主机上的 key 文件被删除或改名后，
   运行中的容器仍挂着原来的 inode，`/run/secrets/vault_master_key` 很可能是原 key 的
   最后一份副本——在重启、重建或 `make prod-down` 之前先把它导出来：

   ```bash
   OUT="$BK/vault_master_key.from-running-host-$(date +%Y%m%d%H%M%S)"
   TMP="$(mktemp "$BK/.vault_master_key.rescue.XXXXXX")" \
     && [ -n "$(docker compose "${F[@]}" ps -q --status running host)" ] \
     && docker compose "${F[@]}" exec -T host cat /run/secrets/vault_master_key > "$TMP" \
     && [ -s "$TMP" ] && PUBLISH "$TMP" "$OUT" \
     || FAILED "未能从运行中的 Host 导出 key（容器未运行、读取失败或内容为空）；临时文件 $TMP 可删除"
   ```

   先写 `mktemp` 出来的 0600 临时文件、确认非空再 `PUBLISH` 到带时间戳的最终名：重试或
   `exec` 中途失败都不会截断上一次成功导出的副本。导出成功即把它放回 `KEY_FILE`
   （`cp` + `chmod 600`），key 没有丢。然后再依次核对：
   §1.1 解析出的 `KEY_FILE`（及其备份）、`VAULT_MASTER_KEY_FILE` 是否把 compose secret
   指到了别的路径、原生形态根 `.env` 的 `AGENT_LEGION_VAULT_MASTER_KEY` /
   `AGENT_LEGION_VAULT_MASTER_KEY_FILE`、密钥保管处。只要找回原 key 放回原位并重启
   Host，一切恢复，无需其他操作。
2. **确认无法找回后再换新 key**。新 key 一旦开始用于写入，旧 key 即使事后找回也
   解不开新写入的密文（单 key 设计，两把 key 不能并存），所以这一步要一次决定。
   生成方式与首次部署相同（见 [agent-worker-deployment.md](agent-worker-deployment.md) §1）。
   顺序是：**先停 Host**（Docker stack：`docker compose "${F[@]}" stop host`；原生形态：
   `make prod-down`）——运行中的 Host 仍持有旧 key（Docker 挂着旧 inode，原生进程持有
   旧环境值），此时并发的 secret 重录或外部连接保存会把新密文写成旧 key 加密，换 key
   后这些刚写入的数据就解不开了，所以在新 key 就位之前不能再有任何 vault 写入；然后
   在同目录的临时文件里生成新 key 并确认非空，再把现有 key 文件（若存在——key 丢失时
   它可能已经不在）改名为带时间戳、且事先不存在的 `.old-<时间戳>` 留存，最后把新 key
   改名就位。任何一步失败都不会截断或覆盖旧 key，重跑也不会拿空文件盖掉上一次留存的
   旧 key（失败留下的 `.vault_master_key.new.*` 临时文件可直接删除）：

   ```bash
   docker compose "${F[@]}" stop host        # 原生形态：make prod-down
   KEY_FILE=<§1.1 中 compose 解析出的绝对路径>; KEY_FILE="${KEY_FILE%/}"
   OLD="$KEY_FILE.old-$(date +%Y%m%d%H%M%S)"
   NEW="$(mktemp "$(dirname "$KEY_FILE")/.vault_master_key.new.XXXXXX")" \
     && UV_CACHE_DIR=.uv-cache uv run python -c \
          "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
          > "$NEW" \
     && [ -s "$NEW" ] && chmod 600 "$NEW" \
     && [ ! -e "$OLD" ] \
     && { [ ! -e "$KEY_FILE" ] || mv "$KEY_FILE" "$OLD"; } \
     && mv "$NEW" "$KEY_FILE" \
     && echo "新 key 已就位：$KEY_FILE（原文件若存在已留存为 $OLD）" \
     || FAILED "检查临时文件 $NEW 与 $KEY_FILE；旧 key 未被覆盖"
   ```

   上面是 Docker stack 的做法：`KEY_FILE` 必须是 compose 实际挂载的文件（部署用
   `VAULT_MASTER_KEY_FILE` 覆盖过来源时，默认的 `deploy/secrets/vault_master_key`
   根本不被读取），按 §1.1 用 `docker compose … config` 解析，不要假定默认路径。
   原生形态 Host 不读 `deploy/secrets/vault_master_key`：用
   `AGENT_LEGION_VAULT_MASTER_KEY_FILE` 的，把 `KEY_FILE` 设为它指向的文件、执行
   同一段命令；用 `AGENT_LEGION_VAULT_MASTER_KEY` 字面值的，先把根 `.env` 留存一份
   （`B=".env.old-$(date +%Y%m%d%H%M%S)"; [ ! -e "$B" ] && cp -p .env "$B"`），再把该
   变量改为新 key（二者择一）。然后启动 Host，记下换 key 的时间，并立刻把新 key
   纳入 §1.1 的备份。原生形态：`make prod-up`。Docker stack **必须强制重建 Host 容器**，
   不能只 `start`：compose secret 是单文件 bind mount，上面的 `mv` 只换了宿主机路径上的
   文件，已停止的容器重新 start 仍挂着被改名的旧 inode；而 compose 的服务配置哈希不含
   secret 源文件内容，`make prod-up docker`（内部是 `docker compose … up -d --build`）在
   Host 配置未变时也不会重建它。用（`F` 见 §1.1）：

   ```bash
   docker compose "${F[@]}" $(./scripts/local-s3-decide.sh --compose-flags --default-endpoint http://seaweedfs:8333 deploy/.env) \
     up -d --no-deps --force-recreate --wait host
   # 两行输出一致才说明 Host 已读到新 key；不一致不要开始下一步重录
   docker compose "${F[@]}" exec -T host cat /run/secrets/vault_master_key | shasum -a 256
   shasum -a 256 < "$KEY_FILE"
   ```

   Linux 上没有 `shasum` 时两处都换成 `sha256sum`。
3. **重新录入全部 secret**（按名称覆盖写入，名称不变，已冻结的 `secret_ref` 在
   重录后即可重新解析）：
   - 外部服务连接：admin 全局设置「外部服务连接」逐个编辑，在 secret 字段输入
     新值保存（`PUT /api/admin/connections/{key}`；只回显「已设置」标记的字段会
     保留旧密文，必须实际输入值），保存会同时清掉该连接的 token 缓存，随后执行
     连接测试确认。
   - 节点配置的 secret 字段：在 workspace 设置页的节点配置里重新填写并保存。
   - 直接经 API 写入的 workspace secret：`GET /api/workspaces/{workspace_id}/secrets`
     列出名称（只有名称与时间戳），逐个 `PUT /api/workspaces/{workspace_id}/secrets/{name}`
     重写。
   - **完成核对**（逐个 workspace）：`GET /api/workspaces/{workspace_id}/secrets`
     列出的全部名称（节点 secret 字段也在其中，名称形如 `node:...`），每一个的
     `updated_at` 都必须晚于换 key 时间；不再需要的用
     `DELETE /api/workspaces/{workspace_id}/secrets/{name}` 删除。剩下任何一个旧
     密文，该 workspace 的引用节点仍会失败，日志脱敏也仍然整体关闭（§4.1）。
4. **补跑失败的 job**：key 失效期间因 secret 解析失败的节点以配置错误失败，
   重录完成后按常规方式重跑。

### 4.3 key 泄露

拿到 key 与数据库（或其备份）的人可以解出全部 secret。处置与 4.2 第 2、3 步
相同：生成新 key、重启，并在**上游服务侧轮换**每一个凭据后用新值重录——仅换
平台 key 而沿用旧凭据，泄露的明文依然有效。注意脱敏只替换 vault 里的**当前**
值，轮换后历史日志里若留有旧值会明文显示。
