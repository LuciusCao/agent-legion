# 备份与恢复 runbook（PostgreSQL / 实例对象存储 / vault 主密钥 / skill 仓库）

一个 Agent Legion 实例的持久状态分以下几块，缺任何一块都不能完整恢复：

| 状态 | 位置（Docker stack，`deploy/compose.host.yaml`） | 内容 |
|---|---|---|
| PostgreSQL | 命名卷 `postgres-data`（服务 `postgres`，库 / 角色均为 `agent_legion`） | 全部业务与执行态：workspace、workflow revision、jobs / node_runs、`materials` 与 `job_artifacts` 清单行、vault 密文（`workspace_secrets` / `instance_secrets` / `connection_tokens`） |
| 实例对象存储 | 默认 SeaweedFS 命名卷 `seaweedfs-data`（`deploy/compose.local.yaml` 可改为 bind-mount，实际来源按 §2 解析；rustfs 逃生舱为 `rustfs-data`；外部 S3 则在对方服务里） | 材料对象（bucket 根）与产物权威副本（`jobs/` 前缀），清单行只存 key |
| vault 主密钥 | `deploy/secrets/vault_master_key`（compose secret，以 `AGENT_LEGION_VAULT_MASTER_KEY_FILE` 注入 Host） | 解开上述全部 vault 密文的唯一 Fernet key，**不在数据库里** |
| skill 仓库 | 宿主机目录 `${AGENT_SKILLS_DIR:-../skills}`（bind mount 到 Host 容器 `/root/.agents/skills`，不在任何命名卷里） | 各 skill 的本地 in-place Git 仓（含 `.git` 历史）与 workspace 的 `_shared` 材料；DB 的 `skill_lock` 只记录 commit，内容无法从 DB 或对象存储重建 |

原生形态（`make prod-up`）下 PostgreSQL 是 `AGENT_LEGION_DATABASE_URL` 指向的
本机实例，vault 主密钥来自根 `.env` 中的 `AGENT_LEGION_VAULT_MASTER_KEY` /
`AGENT_LEGION_VAULT_MASTER_KEY_FILE`（二者择一，见 `.env.example`）。PostgreSQL
本身的运维（版本、跨大版本 dump/restore、连接池）见
[postgresql-runbook.md](postgresql-runbook.md)；对象存储部署与槽位运维见
[materials-storage-deployment.md](materials-storage-deployment.md)；`data/`
目录各子目录的生命周期见 [data-layout.md](data-layout.md)。

## 1. 备份口径

### 1.1 必须备份

- **PostgreSQL 逻辑备份**：`pg_dump -Fc` 全库。客户端版本与服务端同为
  PostgreSQL 17（Docker stack 用容器内自带的 `pg_dump` 即可）。
- **实例对象存储**：材料 bucket（`AGENT_LEGION_S3_BUCKET`，默认
  `agent-legion`）的全部对象。`jobs-staging/` 前缀是 Worker 直传的暂存残留，
  可不备份。
- **vault 主密钥**：备份 Host 进程**实际读取**的那把 key。Docker stack 是
  `deploy/secrets/vault_master_key`（或 `VAULT_MASTER_KEY_FILE` 覆盖的路径）；
  原生形态 Host 只从进程环境 / 根 `.env` 读 `AGENT_LEGION_VAULT_MASTER_KEY`
  （key 字面值）或 `AGENT_LEGION_VAULT_MASTER_KEY_FILE`（所指文件），默认不读
  `deploy/secrets/vault_master_key`（`server/app/services/vault.py` 的
  `resolve_master_key`），备份的是该变量的值或它指向的文件。**与数据库备份分开存放**（例如单独的密钥保管处）：dump +
  key 放在一起，等于把全部 secret 明文交给拿到备份的人；但两者都必须可恢复。
- **skill root**（整个目录，含每个 skill 仓的 `.git` 历史与 workspace 下的
  `_shared`）：skill 只以 skill root 下的本地 in-place Git 仓存在（无注册表、无
  远程 clone 通道），仓缺失即 dispatch 报错；DB `skill_lock` 冻结的是 commit
  sha，pinned ref 的执行要求该 commit 仍在仓里，只备份工作树不够。位置：原生
  形态为 `~/.agents/skills`（`server/app/skills/skill_roots.py`）；Docker stack 为
  `AGENT_SKILLS_DIR` 解析出的宿主机目录，同样让 compose 解析：
  `docker compose "${F[@]}" config | grep -B3 'target: /root/.agents/skills'` 输出的
  `source:`（`F` 见下文 key 文件一段）。下文记作 `SKILLS`，备份命令见 §2.2.2。
- **部署凭据**：`deploy/secrets/postgres_password`、`deploy/secrets/postgres_pgpass`
  与 `deploy/.env`（S3 凭据等）。丢了可以重新生成，但要同步改 PostgreSQL 角色
  密码与对象存储 root 凭据，有备份更省事。

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
KEY_FILE=<上面输出中 file: 后的绝对路径>
```

### 1.2 视实例情况必须备份：只在本地的 legacy 产物

Host 数据根（Docker stack 默认为卷 `host-data`，挂在容器 `/var/lib/agent-legion`，实际来源按 §2 解析；
原生形态为 `data/` 或 `AGENT_LEGION_DATA_DIR`）里有两类内容可能是**唯一副本**，
无法从对象存储重新物化：

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
`docker compose -f deploy/compose.host.yaml exec -T postgres psql -U agent_legion -d agent_legion -c '<SQL>'` 执行）：

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
（第三条只用于了解涉及范围，不要据此只挑部分目录），命令见 §2.2.1。

### 1.3 建议备份

- Worker 的 velites 模型配置：`VELITES_CONFIG_DIR` 目录（`models.json`）与
  `deploy/velites-provider.env`（provider 凭据，0600），都是 gitignored 的本机文件，
  丢失可按 [agent-worker-deployment.md](agent-worker-deployment.md) §2 重配。
  velites 二进制本身不用备份，恢复时从 GitHub Release 重取（§2.3 第 6 步）。
- Worker 状态卷 `worker-control`（状态副本 `worker.yaml`、control token）：丢失
  可按 [agent-worker-deployment.md](agent-worker-deployment.md) 重新配置与注册，
  备份只为省去重配。

### 1.4 不需要备份

在 §1.2 的判定结果为空的前提下，`data/materials_cache/`、`data/jobs/` 下的本地
job / run 目录、`data/agent_bundles/`、`data/logs/`（日志按保留期轮转，有审计需求
再自行归档）以及 Worker 的 work root 都是缓存或在途文件，丢失后按需从对象存储
重新物化或自动重建。

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

以下命令在 prod worktree 根目录执行。compose 文件不是默认文件名，`-f` 不可省；
命名卷的实际名称带 compose 项目名前缀（`agent-legion_`），以
`docker volume ls` 为准。

**先解析数据的实际挂载源**：`agent-legion_seaweedfs-data` / `agent-legion_host-data`
只是基础编排的默认形态。`deploy/compose.local.yaml`（gitignored，Makefile 与
prod-up 入口存在即自动并入）可以把既有数据目录 bind-mount 到 `seaweedfs` 的
`/data` 或 `host` 的 `/var/lib/agent-legion`，这时命名卷是空的或未被使用，照抄
卷名会归档空卷、恢复时写回错误位置。因此这台机器有 `compose.local.yaml` 时要把它
与 `deploy/.env` 一起备份、全新机器上一起先放回。**解析卷源之前先确认
`compose.local.yaml` 已就位，`F` 必须在它就位之后计算**（§1.1 的 `F` 定义在它
不存在时只含基础文件；放回后要重新执行一次，否则解析出的仍是命名卷）。下文的
`docker compose` 命令都用这个 `F`（不带它重建容器会退回命名卷），`docker run -v`
的卷源一律用下面解析出的变量：

```bash
docker compose "${F[@]}" --profile materials-local config seaweedfs host \
  | grep -B2 -A2 -E 'target: /(data|var/lib/agent-legion)$'
# type: bind 时 source 是宿主机绝对路径，直接用；
# type: volume 时 source 只是卷键，实际卷名不要手拼，读顶层 volumes 段该键下的 name:
docker compose "${F[@]}" --profile materials-local config | sed -n '/^volumes:/,/^[a-z]/p'
SW_SRC=<顶层 volumes 里的 name，或 bind 的绝对路径>
HD_SRC=<顶层 volumes 里的 name，或 bind 的绝对路径>
# 命名卷形态先确认卷存在：docker run -v 遇到不存在的卷名会静默新建空卷
docker volume inspect "$SW_SRC" >/dev/null && docker volume inspect "$HD_SRC" >/dev/null
```

bind 形态跳过 `docker volume inspect`，改为确认该目录存在（`[ -d "$SW_SRC" ]`）。
仅在容器已存在时（`docker compose "${F[@]}" ps -aq seaweedfs` 有输出；全新机器
恢复前通常没有）可再用 `docker inspect -f '{{range .Mounts}}{{.Type}} {{.Name}} {{.Source}} -> {{.Destination}}{{println}}{{end}}' "$(docker compose "${F[@]}" ps -aq seaweedfs)"`
（`host` 同理）交叉核对。`docker run -v "$SW_SRC":/data` 对卷名与绝对路径都适用。
下文 tar 打包与解包前都应已通过上面的存在性检查。唯一例外是全新机器恢复：命名卷
尚不存在，`docker volume inspect` 失败属预期，此时确认 `SW_SRC` / `HD_SRC` 与 config
输出的 `name:` 逐字一致后跳过该检查，由 `docker run -v` 按该名称新建（compose 之后
复用同名卷，可能警告该卷不是 compose 创建的，属预期）。

### 2.1 PostgreSQL 备份

先写唯一的临时文件（`mktemp`，权限 0600），`pg_dump` 成功后再改名为最终文件；
最终文件名带到秒，且已存在时拒绝覆盖——重试不会截断上一份成功的备份，失败只
留下以 `.` 开头的临时文件（可直接删除）。命令在 bash 与 zsh 下通用（用函数包装
`docker compose`，原因见 §2.3 第 4 步）：

```bash
C() { docker compose -f deploy/compose.host.yaml exec -T postgres "$@"; }
BK=<备份目录>
mkdir -p "$BK"
OUT="$BK/agent_legion-$(date +%Y%m%d%H%M%S).dump"
TMP="$(mktemp "$BK/.agent_legion-dump.XXXXXX")" \
  && C pg_dump -U agent_legion -d agent_legion -Fc > "$TMP" \
  && [ ! -e "$OUT" ] && mv "$TMP" "$OUT" && echo "备份完成：$OUT" \
  || { echo "未完成：检查临时文件 $TMP（pg_dump 失败，或目标 $OUT 已存在）" >&2; false; }
```

原生形态用本机 PostgreSQL 17 客户端，同样先写临时文件：把上面的
`C pg_dump … > "$TMP"` 换成 `pg_dump -Fc -d "$AGENT_LEGION_DATABASE_URL" -f "$TMP"`。

### 2.2 对象存储备份

二选一：

- **S3 层复制**（热备份可用，也适用于外部 S3）：用 `aws s3 sync` 或 `rclone`
  把 bucket 同步到独立的备份目标（与「迁移后端」同一手段，见
  [materials-storage-deployment.md](materials-storage-deployment.md) §4）。
  恢复时**先**让目标后端运行并建好 bucket，**再**反向同步（`aws s3 sync` 不会
  建 bucket，目标 bucket 不存在时直接失败；bucket 的 CORS 配置也不随对象复制）：
  1. 本地后端先拉起并等到 healthy（`F` 见 §1.1）：
     `docker compose "${F[@]}" --profile materials-local up -d --wait seaweedfs`
     （rustfs 逃生舱为 `--profile materials-local-rustfs up -d --wait rustfs`；外部
     S3 跳过）。不带 `--wait` 时紧接的建 bucket 可能连不上；
  2. 建 bucket 与浏览器直传 CORS。Docker stack 用一次性 Host 容器执行——bucket、
     endpoint、凭据由 compose 按 `deploy/.env` 注入（宿主机直接读 `deploy/.env`
     时 bucket 未显式写出会被当作「未配置」静默跳过）：
     `docker compose "${F[@]}" run --rm --no-deps host python scripts/ensure-s3-bucket.py`。
     Host 容器内 `AGENT_LEGION_S3_ENDPOINT` 默认是 `http://seaweedfs:8333`，rustfs
     部署须已在 `deploy/.env` 覆盖为 `http://rustfs:9000`。全新机器上先按 §2.3
     第 5 步放回 skill root（至少以当前用户 `mkdir -p "$SKILLS"`）再执行这条：
     `run host` 会挂载 `${AGENT_SKILLS_DIR:-../skills}`，绑定源不存在时 Linux 上
     Docker 以 root 创建它，之后普通用户解包会 EACCES；
     原生形态：`UV_CACHE_DIR=.uv-cache uv run python scripts/ensure-s3-bucket.py .env`；
  3. 反向同步（本地后端从宿主机访问：SeaweedFS 为 `http://127.0.0.1:8333`，rustfs
     为 `http://127.0.0.1:9000`）。
- **SeaweedFS 卷级冷备份**：先停对象存储容器，再打包整个 `/data`（filer
  元数据与 volume 文件都在其中，停机打包才自洽）：

  ```bash
  TS="$(date +%Y%m%d%H%M%S)"
  docker compose "${F[@]}" stop seaweedfs
  docker run --rm -v "$SW_SRC":/data:ro -v <备份目录>:/backup \
    busybox sh -c "tar czf /backup/.seaweedfs-data-$TS.partial -C /data . \
      && [ ! -e /backup/seaweedfs-data-$TS.tar.gz ] \
      && mv /backup/.seaweedfs-data-$TS.partial /backup/seaweedfs-data-$TS.tar.gz \
      || { echo '未完成：检查 <备份目录>/.seaweedfs-data-'$TS'.partial（tar 失败或目标已存在）' >&2; false; }"
  docker compose "${F[@]}" --profile materials-local up -d seaweedfs
  ```

  与数据库备份同理：先写临时文件、成功后再改名，不覆盖已有备份。
  `seaweedfs` 挂在 `materials-local` profile 下，单独拉起时要带
  `--profile`（或直接 `make prod-up docker`，由入口按决策加 profile）。

### 2.2.1 legacy 本地产物备份（§1.2 判定非空时）

Host 数据卷里的 `artifacts/` 与整个 `jobs/` 随数据库一起打包，写法同上：

```bash
TS="$(date +%Y%m%d%H%M%S)"
docker run --rm -v "$HD_SRC":/src:ro -v <备份目录>:/backup \
  busybox sh -c "cd /src && tar czf /backup/.host-data-$TS.partial artifacts jobs \
    && [ ! -e /backup/host-data-$TS.tar.gz ] \
    && mv /backup/.host-data-$TS.partial /backup/host-data-$TS.tar.gz \
    || { echo '未完成：检查 <备份目录>/.host-data-'$TS'.partial（tar 失败或目标已存在）' >&2; false; }"
```

热备份时这两处可能有正在写入的文件，强一致按 §1.5 先停 Host 与 Worker。原生
形态直接打包数据根下的 `artifacts/` 与 `jobs/`。某个目录不存在（例如从未写过 legacy CAS）时从命令里去掉它。

### 2.2.2 skill root 备份

在宿主机上直接打包 `SKILLS`（§1.1；Docker 的挂载是宿主机目录，不需要进容器），
同样先写临时文件、成功后再改名：

```bash
SKILLS=<§1.1 解析出的 skill root>
BK=<备份目录>
OUT="$BK/skills-$(date +%Y%m%d%H%M%S).tar.gz"
TMP="$(mktemp "$BK/.skills-tar.XXXXXX")" \
  && tar czf "$TMP" -C "$SKILLS" . \
  && [ ! -e "$OUT" ] && mv "$TMP" "$OUT" && echo "备份完成：$OUT" \
  || { echo "未完成：检查临时文件 $TMP（tar 失败，或目标 $OUT 已存在）" >&2; false; }
```

`-C "$SKILLS" .` 会带上 `.git` 等隐藏目录。打包期间不要
经平台编辑 skill 文件或 `_shared` 材料（会写仓库），否则可能打进半提交的 Git 状态。

### 2.3 恢复

恢复只能落到**同版本或更新版本**的代码上：启动时 `init_db` 只向前迁移
（`schema_migrations` 已记录到当前 `SCHEMA_VERSION` 即 no-op），不存在降级
路径——把新版本 dump 恢复给旧代码属于不受支持的形态。

1. 停 Host 与 Worker，避免恢复期间有写入：
   `docker compose "${F[@]}" stop host worker`。
2. 全新机器先放回部署凭据：clean checkout 里没有 gitignored 的 `deploy/.env` 与
   `deploy/secrets/`，而 `postgres` 服务经 `POSTGRES_PASSWORD_FILE` 挂载
   compose secret `postgres_password`，文件缺失时容器起不来。把 §1.1 备份的
   `deploy/.env`、`deploy/secrets/postgres_password`、`deploy/secrets/postgres_pgpass`
   放回原位（`chmod 600`；`deploy/.env` 若用 `POSTGRES_PASSWORD_FILE` /
   `POSTGRES_PGPASS_FILE` 改写了来源，放到改写后的路径）。`deploy/.env` 要先于
   下一步放回：它可能用 `VAULT_MASTER_KEY_FILE` 改写 key 路径。原机有
   `deploy/compose.local.yaml` 的同样先放回（并按它重建 bind-mount 的宿主机目录）。
   **放回之后重新执行 §1.1 的 `F` 定义与 `KEY_FILE` 解析**（第 1 步时它可能还不
   存在，旧 `F` 只含基础文件：用它解析卷源、拉起 postgres 会把数据恢复进命名卷，
   而第 6 步入口读到 `compose.local.yaml` 改挂 bind 目录，恢复后数据为空）；之后
   才按 §2 开头解析 `SW_SRC` / `HD_SRC`，第 3 步起的命令都用新 `F`。
3. 恢复 vault 主密钥：把备份的 key 放回 Host 实际读取的位置（Docker stack 为
   §1.1 用 `docker compose … config` 解析出的 `KEY_FILE`，默认即
   `deploy/secrets/vault_master_key`，`chmod 600`；原生形态见 §1.1）。**不要**在
   缺 key 文件或部署凭据的状态下运行 `scripts/install-deps.sh` 或
   `scripts/init-worktree.sh`：二者在 key 文件缺失或为空时会生成一把新 key，新
   key 解不开备份里的任何密文。全新机器上这时再只拉起数据库：
   `docker compose "${F[@]}" up -d postgres`（后续 `exec` 需要
   容器在运行）。
4. 先预检 dump，再把现库**改名保留**（不要 drop），然后建空库、整事务导入。
   旧库保留期间新旧两份数据并存，PostgreSQL 数据卷所在磁盘需要约两倍库体积
   的空闲空间。以下命令在 bash 与 zsh 下都可直接执行（用函数而不是字符串变量
   包装 `docker compose`，zsh 不会对未加引号的变量分词），各步以 `&&` 串联，
   任何一步失败即停止：

   ```bash
   C() { docker compose -f deploy/compose.host.yaml exec -T postgres "$@"; }
   DUMP=<备份目录>/agent_legion-<时间戳>.dump
   # 预检：把整个归档解码为 SQL 丢弃，能读完说明文件完整（--list 只读头部与目录）
   C pg_restore -f /dev/null < "$DUMP" \
     && C psql -U agent_legion -d postgres -v ON_ERROR_STOP=1 \
          -c 'ALTER DATABASE agent_legion RENAME TO agent_legion_pre_restore' \
     && C createdb -U agent_legion -O agent_legion agent_legion \
     && C pg_restore -U agent_legion -d agent_legion --no-owner \
          --exit-on-error --single-transaction < "$DUMP"
   ```

   预检失败时后续步骤都不会执行，现库原样不动。`pg_restore` 默认遇错继续、只在
   结尾报错数，`--exit-on-error --single-transaction` 让任何一条失败都整体回滚，
   不会留下半导入的库。改名之后的步骤失败时，用下面两条命令回到恢复前状态：

   ```bash
   C dropdb -U agent_legion agent_legion
   C psql -U agent_legion -d postgres -v ON_ERROR_STOP=1 \
     -c 'ALTER DATABASE agent_legion_pre_restore RENAME TO agent_legion'
   ```

   旧库保留到 §2.4 全部核对通过后再删除：
   `C dropdb -U agent_legion agent_legion_pre_restore`（`C` 即上面定义的函数）。
5. 恢复对象存储：S3 层按 §2.2 的顺序（先拉起后端、建 bucket，再反向同步）；
   卷级备份则停 `seaweedfs` 后清空卷内容再解包。**先完整校验归档再删**：
   `tar tzf` 读完整个 gzip 流，截断或损坏的包会在这里失败，`find` 不会执行、
   现有卷原样不动；清空用 `find -mindepth 1 -delete`（`rm -rf /data/*` 不会删
   隐藏文件）。现有卷还有可能需要的数据时，先按 §2.2 再冷备一份当前卷，与
   数据库恢复保留旧库同理：
   `docker run --rm -v "$SW_SRC":/data -v <备份目录>:/backup busybox sh -c 'A=/backup/seaweedfs-data-<时间戳>.tar.gz; tar tzf "$A" >/dev/null && find /data -mindepth 1 -delete && tar xzf "$A" -C /data'`。
   有 §2.2.1 的 legacy 本地产物备份时一并放回 Host 数据卷，做法与上面对称：
   先 `tar tzf` 完整校验，成功后再删掉卷里现有的 `artifacts/` 与 `jobs/`，最后解包。
   不清空的话，备份之后才写入的文件会留在原处，而产物读取优先看本地 job_dir，
   恢复后的数据库会读到新旧混合的内容；包损坏时也不会解到一半才失败。两个目录
   按备份时的状态整体替换（备份里没有 `artifacts/` 说明当时就没有 legacy CAS，
   删掉现有的同样正确）。现有卷里的这两个目录还可能有用时，先按 §2.2.1 再打一份：
   `docker run --rm -v "$HD_SRC":/dst -v <备份目录>:/backup busybox sh -c 'A=/backup/host-data-<时间戳>.tar.gz; tar tzf "$A" >/dev/null && rm -rf /dst/artifacts /dst/jobs && tar xzf "$A" -C /dst'`。
   原生形态同样在数据根上先校验、再删 `artifacts/` 与 `jobs/`、再解包。
   恢复 skill root：在宿主机上把 §2.2.2 的包解到 `SKILLS`（目标目录应为空或不存在；
   Docker stack 须是 compose 解析出的同一挂载源）：
   `mkdir -p "$SKILLS" && tar xzf <备份目录>/skills-<时间戳>.tar.gz -C "$SKILLS"`。
6. 全新机器先备好 Worker 的 velites 二进制：它不在仓库、镜像与本 runbook 的备份
   里（`velites-bin/` 是 gitignored 目录），而 compose 把
   `${VELITES_BIN:-../velites-bin/velites}`（默认即 `<仓库根>/velites-bin/velites`）
   挂进 Worker。文件缺失时 Docker 会在该路径建一个空目录，容器照常创建，随后
   期望 runtime 守卫（`AGENT_WORKER_EXPECT_RUNTIMES`，默认 `velites`）探测不到
   velites，Worker 以退出码 2 退出，下面入口的 `--wait` 随之失败。仓库没有自动
   下载入口（`Makefile` 与 `scripts/stack-prod-up.sh` 不处理它，
   `scripts/ensure-velites.sh` 只为裸机形态从源码构建到 `data/bin`），按
   [agent-worker-deployment.md](agent-worker-deployment.md) §5「velites 二进制来源」
   从 GitHub Release（`velites-v*`）取与宿主机架构一致的产物，放到上述路径并
   `chmod +x`；`deploy/.env` 用 `VELITES_BIN` 改过位置的放到改写后的路径。已经在
   缺文件的状态下启动过的，先 `rmdir` 掉 Docker 建的空目录再放文件。Worker 的
   模型配置 `VELITES_CONFIG_DIR`（`models.json`）与 `deploy/velites-provider.env`
   同样不在仓库里，按 §1.3 的备份放回，或按 agent-worker-deployment.md §2 重配。
   原机本就是零 runtime 形态（去掉 velites 挂载的 override、`deploy/.env` 里
   `AGENT_WORKER_EXPECT_RUNTIMES=` 置空）的，这两处已随第 2 步放回，无需二进制。
   然后 `make prod-up docker` 拉起整个 stack；低于当前版本的 dump 会在启动时自动
   迁移到当前 schema。注意该入口（`scripts/stack-prod-up.sh`）启动前**无条件**检查
   默认路径 `deploy/secrets/{postgres_password,postgres_pgpass,vault_master_key}`
   非空，不看 `POSTGRES_PASSWORD_FILE` / `POSTGRES_PGPASS_FILE` /
   `VAULT_MASTER_KEY_FILE` 覆盖（compose 实际挂载的仍是覆盖后的路径）。用了覆盖的
   部署二选一：在默认路径也放一份同内容文件（`chmod 600`，仅为通过预检，换 key 后
   要同步更新），或跳过入口直接执行它的等价命令（`F` 见 §1.1）：
   `docker compose "${F[@]}" $(./scripts/local-s3-decide.sh --compose-flags --default-endpoint http://seaweedfs:8333 deploy/.env) up -d --build --wait`。
7. 后端每次启动都会把全部 workspace 调度重置为暂停（`server/app/main.py` 启动时
   调用 `reset_all_to_paused`），恢复后先完成 §2.4 的核对，再经控制台恢复调度。

### 2.4 恢复后核对

- `GET /api/health` 的 `storage.reachable` 为真；admin 基础设施连接探测
  （`POST /api/admin/infra-connections/test`，`target` 分别取 `database` / `storage`）显示数据库与对象存储均可达。
- 对每个外部服务连接执行一次测试（admin 全局设置「外部服务连接」，或
  `POST /api/admin/connections/{key}/test`）：它会解析实例 vault 中的凭据，
  是验证 vault 主密钥与数据库匹配的最直接手段。key 对不上时该接口返回 HTTP 500
  （凭据解析在探测之前抛错），而不是 `ok: false`。
- 热备份恢复的实例：`scripts/gc-s3-jobs.py` 默认 dry-run（Docker stack 在 Host
  容器内执行：`docker compose -f deploy/compose.host.yaml exec host python scripts/gc-s3-jobs.py`），先只看报告——列出的
  是 dump 之后写入、清单里没有行的孤儿对象，确认无误后再加 `--apply`。「行在、
  对象缺失」的产物会让依赖它的下游节点停在等待中，job 详情页对应节点显示
  「输入恢复不全，建议重跑 <生产节点>」，按提示重跑生产节点即可。
- skill 锁定 commit 可物化：DB `global_settings` 中 `skill_lock` 文档（JSON，
  `skills.<skill key>.refs.<ref> = <commit>`）记录的每个 commit 都必须存在于
  `SKILLS/<skill key>` 仓里（`server/app/skills/lock.py` 的仓位置约定）。逐个用
  `git cat-file -e` 核对（`C` 为 §2.3 第 4 步定义的函数；`latest` ref 跟随 HEAD、
  不进锁，仓存在即可）：

  ```bash
  C psql -U agent_legion -d agent_legion -At \
      -c "select value from global_settings where key = 'skill_lock'" \
    | python3 -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); [print(k, c) for k, s in d.get("skills", {}).items() for c in s.get("refs", {}).values()]' \
    | while read -r key commit; do
        git -C "$SKILLS/$key" cat-file -e "$commit^{commit}" \
          && echo "ok $key $commit" || echo "缺失 $key $commit" >&2
      done
  ```

  有「缺失」即说明 skill 备份不是锁定时刻之后的版本，或仓的历史被改写过，需要
  找回含该 commit 的仓。**不要用 `make skills-lock` 做这项核对**：它会把每个已
  pin 的 ref 重新解析到仓里的当前 commit 并改写锁（`server/app/skills/lock.py`），
  等于用恢复后的仓覆盖锁定记录，掩盖缺失。

## 3. 演练频率建议

- 每次涉及 schema 迁移的升级前，确认有一份当天的数据库备份（迁移幂等可重入，
  但只向前）。
- 每季度至少做一次完整恢复演练：在与生产隔离的环境（另一台机器，或开发机上
  的派生库与派生 bucket，不要碰共享 / prod 库）用最近一份备份走完 §2.3 与
  §2.4，其中必须包含「用备份的 vault 主密钥通过一次外部服务连接测试」——只恢复
  了数据库、key 却对不上，是演练最常暴露的问题。
- 备份任务本身要有失败告警，并定期抽查备份文件可被 `pg_restore --list` 正常读取。
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

1. **先找 key，不要急着生成新 key**。依次核对：§1.1 解析出的 `KEY_FILE`（及其
   备份）、`VAULT_MASTER_KEY_FILE` 是否把 compose secret 指到了别的路径、原生
   形态根 `.env` 的 `AGENT_LEGION_VAULT_MASTER_KEY` / `AGENT_LEGION_VAULT_MASTER_KEY_FILE`、
   密钥保管处。只要找回原 key 放回原位并重启 Host，一切恢复，无需其他操作。
2. **确认无法找回后再换新 key**。新 key 一旦开始用于写入，旧 key 即使事后找回也
   解不开新写入的密文（单 key 设计，两把 key 不能并存），所以这一步要一次决定。
   生成方式与首次部署相同（见 [agent-worker-deployment.md](agent-worker-deployment.md) §1）。
   顺序是：先在同目录的临时文件里生成新 key 并确认非空，再把现有 key 文件（若
   存在——key 丢失时它可能已经不在）改名为带时间戳、且事先不存在的 `.old-<时间戳>`
   留存，最后把新 key 改名就位。任何一步失败都不会截断或覆盖旧 key，重跑也不会
   拿空文件盖掉上一次留存的旧 key（失败留下的 `.vault_master_key.new.*` 临时文件可直接删除）：

   ```bash
   KEY_FILE=<§1.1 中 compose 解析出的绝对路径>
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
     || { echo "未完成：检查临时文件 $NEW 与 $KEY_FILE；旧 key 未被覆盖" >&2; false; }
   ```

   上面是 Docker stack 的做法：`KEY_FILE` 必须是 compose 实际挂载的文件（部署用
   `VAULT_MASTER_KEY_FILE` 覆盖过来源时，默认的 `deploy/secrets/vault_master_key`
   根本不被读取），按 §1.1 用 `docker compose … config` 解析，不要假定默认路径。
   原生形态 Host 不读 `deploy/secrets/vault_master_key`：用
   `AGENT_LEGION_VAULT_MASTER_KEY_FILE` 的，把 `KEY_FILE` 设为它指向的文件、执行
   同一段命令；用 `AGENT_LEGION_VAULT_MASTER_KEY` 字面值的，先把根 `.env` 留存一份
   （`B=".env.old-$(date +%Y%m%d%H%M%S)"; [ ! -e "$B" ] && cp -p .env "$B"`），再把该
   变量改为新 key（二者择一）。然后重启 Host，记下换 key 的时间，并立刻把新 key
   纳入 §1.1 的备份。原生形态：`make prod-down && make prod-up`。Docker stack
   **必须强制重建 Host 容器**：compose secret 是单文件 bind mount，上面的 `mv` 只
   换了宿主机路径上的文件，运行中的容器仍挂着被改名的旧 inode；而 compose 的服务
   配置哈希不含 secret 源文件内容，`make prod-up docker`（内部是
   `docker compose … up -d --build`）在 Host 配置未变时不会重建它。用（`F` 见 §1.1）：

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
