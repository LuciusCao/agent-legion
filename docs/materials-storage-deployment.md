# 材料存储（SeaweedFS / RustFS / S3）部署与运维

Agent Legion 的材料（用户上传文件）与后续的 job 产物统一存放在 S3 兼容
对象存储中。**默认自托管 [SeaweedFS](https://github.com/chrislusf/seaweedfs)**
（Apache 2.0，volume/needle 布局，为海量小文件设计）；RustFS 保留为可切换
的逃生舱后端（存量卷迁移期或大对象为主的部署使用）。代码只对 S3 API
编程，可平行切换 Amazon S3 / MinIO / Garage。现行模型见
[docs/architecture/materials-and-runs.md](architecture/materials-and-runs.md)（设计背景见其中链接的历史设计稿）。

## 双后端适用场景

| 后端 | 适用场景 | 原因 |
|---|---|---|
| **SeaweedFS（默认）** | 海量小文件（本仓库主形态：job 产物 JSON / 材料文本，KB 级小对象占绝大多数） | volume/needle 布局把小对象顺序写进 2GB volume 大文件，元数据在 filer DB——对象数增长只加 filer 行，不加磁盘目录项；闲置开销与对象数解耦 |
| RustFS（逃生舱） | 大对象为主的部署（MB 级以上材料占主导、对象总数不高的材料库，如视频/模型文件） | MinIO 血统的 erasure-code 布局对大对象吞吐友好。注意：scanner 的元数据遍历成本仍随**对象总数**增长（与单对象大小无关），只是大对象不含内联数据、避免数据被重读——本仓库 compose 已显式关闭 rustfs 的 scanner/heal（见 §1 表格与 #344），下述成本适用于启用 scanner 或外部 RustFS 部署，且关闭即放弃 heal/用量巡检；本表界定的是「小对象密集」与「对象数有限的大文件」两个极端的相对偏好 |

选择默认后端的依据（#340 调研）：MinIO 血统的 RustFS 把对象存为「一目录 +
一 xl.meta」，后台 scanner 的用量巡检要遍历全命名空间并读取每个对象的
xl.meta——小对象（≤128KiB）数据内联其中，「巡检」字面等于重读全部数据，
闲置 CPU/IO 随对象数线性增长（百万对象级的命名空间下可出现零流量空转多日的
现象）。SeaweedFS
的元数据在 filer 数据库里、数据在 volume 大文件中仅存指针，结构性不存在
该问题（实测对象数 ×4 闲置开销持平，全量列举约 2.5 倍速）。详见 issue
#340 的调研评论。

## 产物 gzip 压缩（#338）与后端选择

#338 起产物走「Worker 上传前 gzip 压缩、读取侧透传 + 客户端解压」（v4+
协议 Worker 才压缩；旧协议 Worker 保持裸对象，读取侧双形态兼容，混编
fleet 不会错配存储形态）——压缩发生在对象层**之外**
（`worker/artifact/gzip.py`，对象本体就是 gzip 字节流），不依赖任何后端的
透明压缩 / `Content-Encoding` 元数据语义。对 SeaweedFS 无负面影响，且方向
上互相强化：压缩把 KB 级 JSON 产物进一步缩小，正是 SeaweedFS volume/needle
布局最擅长的小对象形态；presigned GET 只签 Bucket+Key、PUT 另把 `If-None-Match: *`
签进 SignedHeaders（材料 create-only 条件写，#1321，`s3_client.py`），
gzip 对象的上传下载对后端完全透明。`Content-Encoding: gzip` 响应头由 Host
路由层根据 key 的 `.gz` 后缀自行添加（`job_artifact_raw.py` 判定 +
`job_artifact_raw_response.py` 加头），同样不经后端。

## 后端契约要求（#1279 材料 ETag 协议，M30 R2；#1321 条件写）

材料身份协议对后端的三条硬要求（支持矩阵内的 AWS S3 / MinIO / SeaweedFS
均满足；换后端时必须核对）：

- **HEAD 恒返回非空 ETag**：complete/inline/demo seed 对空 ETag fail-closed
  （不落 ready 的无身份行）；
- **单 GET 原子快照**：`open_stream` 的 If-Match 只校验 GET open 时刻，
  返回的流必须是 open 时刻对象的原子快照（AWS S3 语义）——流读期间的
  同尺寸覆盖对无 hash 材料依赖此契约才不可撕裂；hash 行由下载后的
  sha256 复算免疫，不依赖此条。
- **PUT 条件写（If-None-Match）**：材料 presigned PUT 把 `If-None-Match: *`
  签进 SignedHeaders（#1321），后端必须对既有 key 的条件 PUT 返回
  412 PreconditionFailed、对缺该头的请求返回 403 SignatureDoesNotMatch——
  「complete 之后复用同一 URL 覆盖」在存储层一次性关闭。SeaweedFS 4.45
  实测（#1319，本机 materials-local 栈复核）：签入后缺头 PUT → 403、
  首发 → 200、同 URL 重放 → 412；未签名的 `If-None-Match` 头同样被强制
  执行（对升级前签出的存量 URL，新前端带头 PUT 也获得 412 语义）；
  `CopyObject` 支持 `x-amz-copy-source-if-match`。RustFS 实测待办
  （逃生舱形态接入前必须补做）。

## 1. 组件与配置面

| 项 | 值 | 说明 |
|---|---|---|
| 服务 | compose `seaweedfs`（默认，挂 `materials-local` profile）/ compose `rustfs`（逃生舱，挂 `materials-local-rustfs` profile） | 两者均在 `deploy/compose.host.yaml`。seaweedfs：S3 API `:8333`、master UI `:9333`，数据卷 `seaweedfs-data`；rustfs：S3 API `:9000`、console `:9001`，数据卷 `rustfs-data`。默认 `docker compose up` 不拉起，由 prod-up 入口按 `AGENT_LEGION_LOCAL_S3` 决策后加对应 `--profile` |
| `AGENT_LEGION_LOCAL_S3_BACKEND` | `seaweedfs`（默认）/ `rustfs` | 本地后端选择（#340）。切 rustfs 时同步覆盖 `AGENT_LEGION_S3_ENDPOINT=http://rustfs:9000` 与 `AGENT_LEGION_S3_PUBLIC_ENDPOINT=http://127.0.0.1:9000`；非法值 fail-fast |
| `AGENT_LEGION_LOCAL_S3` | `auto`（默认）/ `always` / `never` | 本地对象存储三态开关，判断逻辑见 `scripts/local-s3-decide.sh`（`make prod-up [docker]`、`make dev-up` 与底层 `make stack-host-up` 共用；各入口对决策失败的处理不同，见下文）。`auto`：endpoint 指向本机或未配置任何 S3 → 启动；endpoint 远程，或只配 bucket/凭据不配 endpoint（AWS 默认端点写法）→ 跳过并打一行原因日志 |
| `AGENT_LEGION_S3_ENDPOINT` | docker stack 默认注入 `http://seaweedfs:8333`（可在 `deploy/.env` 覆盖；显式空值 = AWS S3 默认端点） | 后端直连地址；远程地址会让 `auto` 跳过本地后端。存量 rustfs 用户的 endpoint 仍指向 `http://rustfs:9000` 时，decide 脚本会提示显式配 `AGENT_LEGION_LOCAL_S3_BACKEND=rustfs` |
| `AGENT_LEGION_S3_PUBLIC_ENDPOINT` | compose 默认 `http://127.0.0.1:8333` | presigned URL 的签发地址，必须浏览器 / remote worker 可达；留空则回落用内部 endpoint 签发。SigV4 把 Host 签进签名，签后不可改写——切后端时务必与实际服务的端口一致；不能写 compose 服务名（如 `http://seaweedfs:8333`），宿主机外与隔离网络里的 worker 都解析不到 |
| `AGENT_LEGION_S3_BIND` | 默认 `127.0.0.1` | 本地后端端口（seaweedfs `8333`/`9333`，rustfs `9000`/`9001`）在宿主机上的发布地址，两种 prod 形态都生效（端口发布始终由 compose 托管）。默认只有本机可达。远程 Worker 或其他设备的浏览器要访问时，**只改发布地址与 `AGENT_LEGION_S3_PUBLIC_ENDPOINT`，`AGENT_LEGION_S3_ENDPOINT` 保持本机/内网地址**：Docker 形态 bind 可设为部署机的 LAN / Tailnet 地址或 `0.0.0.0`（host 走 compose 内网 `seaweedfs:8333`，不受影响）；原生形态设为 `0.0.0.0`，这样根 `.env` 的 `http://127.0.0.1:8333` 仍可达（设为具体 IP 会让 `127.0.0.1` 映射消失）。`PUBLIC_ENDPOINT` 指向客户端实际可达的地址。不要把 `AGENT_LEGION_S3_ENDPOINT` 改成 LAN / Tailnet IP：`scripts/local-s3-decide.sh` 只把 `127.0.0.1` / `localhost` / `::1` / `seaweedfs` / `rustfs` 认作本地，其余按外部存储跳过启动本地后端；原生形态确需绑定具体 IP 并让后端走该地址时，须同时设 `AGENT_LEGION_LOCAL_S3=always`。**安全提示**：bind 不只发布 S3 API，还同时发布 seaweedfs 的 `:9333` master UI（rustfs 为 `:9001` console）；设为 `0.0.0.0` 会把它们暴露到所有网卡，须先在主机防火墙或 Tailnet ACL 中限制来源，不要暴露到公网。更窄的做法是只绑具体 Tailnet IP（Docker 形态直接设即可；原生形态需把 `AGENT_LEGION_S3_ENDPOINT` 一并改为该地址并设 `AGENT_LEGION_LOCAL_S3=always`）。写在 `deploy/.env` |
| `AGENT_LEGION_S3_BUCKET` | 默认 `agent-legion` | 每个部署实例一个 bucket；dev worktree 派生 `agent-legion-<worktree>` |
| `AGENT_LEGION_S3_ACCESS_KEY` / `AGENT_LEGION_S3_SECRET_KEY` | 本地后端形态必填；外部 S3 走默认凭据链时留空 | compose 只做 `${}` 字面插值，`deploy/.env` 必须写字面值；`_FILE` 变体仅原生形态可用。compose 同时把它注入本地后端容器作为其 root 凭据 |
| `AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES` | 默认 50GiB | 节点物化缓存（`data/materials_cache/`）容量上限，LRU 淘汰 |

凭据是实例级 infra 配置（与 `database.url` 同级），env-only 注入，不落
tracked yaml、DB、API 或日志（MATERIAL-SECRET-001）。

未配置 `AGENT_LEGION_S3_BUCKET` 时服务整体照常启动，只有 materials/runs
上传相关 API 返回 503（优雅降级）。**Docker 形态下决策为启动本地后端
但凭据未配齐时，`make prod-up docker`（`scripts/stack-prod-up.sh` 调
`scripts/local-s3-decide.sh`）fail-fast**——两个后端留空凭据都会回落镜像
默认的公开凭据，必须拦住；部署前先配好 `deploy/.env`。底层的
`make stack-host-up` 把 decide 放在命令替换里、退出码被吞，凭据缺失时不会
拦住，只会静默不带本地后端启动，部署一律走 `make prod-up docker`。

## 2. 开发形态（make install / make dev-up）

开发路径与生产部署分开：全新 clone 后 `make install`（`scripts/install-deps.sh`，
幂等）一键装齐前置依赖并初始化项目——uv sync、建 `agent_legion_dev` 库、从
`.env.example` 生成 `.env` 并填入随机 `AGENT_LEGION_S3_ACCESS_KEY/SECRET_KEY`
（同时作为本地后端容器的 root 凭据；`.env` 已存在但凭据为空时会幂等补填）、
生成 `deploy/secrets/vault_master_key`、构建 velites 二进制、装前端依赖、
种子 worker 状态副本 `data/agent-worker-service/worker.yaml`。

`make dev-up`（`scripts/dev_stack.sh`）启动开发进程前先经
`scripts/local-s3-decide.sh .env` 决策本地对象存储（与 prod 入口同一套
`AGENT_LEGION_LOCAL_S3` 三态逻辑 + `AGENT_LEGION_LOCAL_S3_BACKEND` 后端
选择）：决策为 start 时从根 `.env` 显式 export
S3 凭据后 `docker compose -f deploy/compose.host.yaml up -d <所选服务>`
（默认 seaweedfs，端口 8333）——
**dev 形态只有根 `.env` 一份配置**（不读 `deploy/.env`），凭据经环境变量
注入 compose 插值，因此不存在「两处凭据不一致」的坑。**本地存储容器已在
运行时 dev-up 跳过 recreate 直接确认 bucket**：compose 项目名固定
`agent-legion`，全机（prod 与所有 worktree）共享同一个容器，凭据不同的
`up -d` 会 recreate 容器并打断持旧凭据的一方；容器在但凭据不匹配会在建
bucket 处告警暴露。起完后调用
`scripts/ensure-s3-bucket.py` 确保 bucket 与浏览器直传 CORS 就绪
（与 `init-worktree.sh` 共用同一脚本）。docker 缺失、启动失败或建 bucket
失败都只告警不阻断：材料 API 降级为 503，其余功能不受影响，就绪后重跑
`make dev-up` 可补齐存储；若期间跳过了 demo 示例材料播种，需另跑一次
`make import-demo`（幂等）补播种——`make dev-up` 本身不会重播材料。
`make dev-status` 会顺带显示所选后端容器状态。

开发环境切外部对象存储：改根 `.env` 的 `AGENT_LEGION_S3_ENDPOINT` /
凭据 / `AGENT_LEGION_S3_BUCKET` 三样即可（AWS 默认端点写法是显式留空
endpoint），`auto` 决策会自动跳过本地后端；细节同 §3.1.1。

## 3. 首次部署 / 升级启用步骤

### 3.1 准备凭据与配置

compose 对 host 与两个本地后端都是 `${AGENT_LEGION_S3_ACCESS_KEY}` 字面插值，
**不支持 `_FILE` 变体**（写了会把路径字符串当 access key 注入）。凭据
直接以字面值写进 `deploy/.env`（该文件已被 git/docker 忽略，与
`LLM_GATEWAY_TOKEN` 同一通道）：

```bash
cd <prod worktree>
umask 077
# 只在首次配置时生成：deploy/.env 里 bucket / endpoint / 凭据任一已配置就不
# 追加——重复键会让后出现的随机值生效（本地后端随之换凭据，外部 S3 直接失效）。
# 随机值先落变量并判非空，生成失败显式报错、不写 .env。grep 的退出码紧随其
# 后判定：1 = 文件可读但无键，文件不存在（grep 报 2）也算首次配置——两者都追
# 加；文件存在但读失败（权限等，rc=2）则中止，不盲目追加。
if grep -qE '^AGENT_LEGION_S3_(BUCKET|ENDPOINT|ACCESS_KEY|SECRET_KEY)=' deploy/.env 2>/dev/null; then
  echo 'deploy/.env 已配置 AGENT_LEGION_S3_*，未改动（换凭据请手工编辑）' >&2
elif [ $? -eq 1 ] || [ ! -e deploy/.env ]; then
  if ACCESS_KEY=$(openssl rand -hex 20) && SECRET_KEY=$(openssl rand -hex 40) &&
      [ -n "$ACCESS_KEY" ] && [ -n "$SECRET_KEY" ]; then
    cat >> deploy/.env <<EOF
AGENT_LEGION_S3_BUCKET=agent-legion
AGENT_LEGION_S3_ACCESS_KEY=$ACCESS_KEY
AGENT_LEGION_S3_SECRET_KEY=$SECRET_KEY
EOF
  else
    echo '生成 S3 凭据失败，deploy/.env 未改动' >&2
  fi
else
  echo 'deploy/.env 存在但读取失败（权限？），未改动' >&2
fi
chmod 600 deploy/.env
```

浏览器 / remote worker 从宿主机外访问时，再把 presigned URL 的签发地址
覆盖为可达地址（默认 `http://127.0.0.1:8333`，匹配 seaweedfs 端口映射）：

```bash
umask 077
# 已有该键就替换那一行、没有才追加（重复键时哪一行生效不直观）；读不到
# 现有 .env 时中止，不拿空内容覆盖它
set_env() {  # set_env <文件> <键> <值>
  if [ -e "$1" ]; then
    grep -v "^$2=" "$1" > "$1.tmp"
    [ $? -le 1 ] || { rm -f "$1.tmp"; echo "读取 $1 失败，未改动" >&2; return 1; }
  else
    : > "$1.tmp"
  fi
  printf '%s=%s\n' "$2" "$3" >> "$1.tmp" && mv "$1.tmp" "$1"
}
set_env deploy/.env AGENT_LEGION_S3_PUBLIC_ENDPOINT 'http://<宿主机地址>:8333'
```

（原生形态 `make prod-up` 的后端/worker 是本机进程，不经 compose：同名
变量**只写 prod worktree 根的 `.env` 一份**——原生加载支持 `_FILE` 变体。
本地后端容器不用手工起，也不用在 `deploy/.env` 重复凭据：`native-prod-up.sh`
经 `scripts/local-s3-decide.sh` 决策为启动时，把根 `.env` 的凭据经子进程
环境注入 `docker compose -f deploy/compose.host.yaml up -d <seaweedfs|rustfs>`
（#624；幂等，docker 不可用或启动失败仅告警，材料 API 降级为 503，其余功能
不受影响）。原生形态的 `AGENT_LEGION_S3_ENDPOINT` 默认指向
`http://127.0.0.1:8333`（rustfs 逃生舱为 `:9000`），
`AGENT_LEGION_S3_PUBLIC_ENDPOINT` 指向浏览器 / remote worker 可达的地址；
端口发布地址仍由 `deploy/.env` 的 `AGENT_LEGION_S3_BIND` 控制，见 §1。）

### 3.1.1 使用外部对象存储（AWS S3 / MinIO / Garage）

代码只对 S3 API 编程，外部存储只需改配置，不需要改代码：

1. 在 `deploy/.env`（docker 形态，compose 插值只读它）或根 `.env`
   （原生形态）配置外部存储：
   - 自建 S3 兼容服务：`AGENT_LEGION_S3_ENDPOINT=https://<外部地址>` +
     bucket + 凭据；
   - AWS S3：显式留空 endpoint（`AGENT_LEGION_S3_ENDPOINT=`，docker 形态
     空值会盖掉 compose 注入的本地后端默认值）+ bucket，凭据写静态
     key 或全留空走 boto3 默认凭据链（IAM role 等）；
   - `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 同步指向客户端可达地址（AWS 默认
     端点场景同样显式留空）。
2. 本地对象存储会随 `auto` 决策自动跳过（endpoint 远程或只配 bucket/凭据
   时 skip，并打一行原因日志）；也可用 `AGENT_LEGION_LOCAL_S3=never`
   显式关闭，`always` 则恢复旧版无条件启动。
3. auto 误判不会静默失败：后端启动自检（probe 的 DEGRADED 日志）与
   `/api/health` 的 `storage.reachable` 会暴露。

### 3.2 启动与建 bucket

```bash
git pull
make prod-up            # 或 make prod-up docker
```

prod 入口只负责把本地后端容器带起来，**不建 bucket**（dev 环境由
`init-worktree.sh` / `make dev-up` 自动完成）。首次启动后用
`scripts/ensure-s3-bucket.py` 建 bucket 并配置浏览器直传 CORS——它幂等，
已存在的 bucket 与已有 CORS 规则都保留，可随时重跑：

```bash
# 原生形态：读根 .env 的 AGENT_LEGION_S3_*（endpoint 默认 http://127.0.0.1:8333）
PYTHONPATH=. UV_CACHE_DIR=.uv-cache uv run python scripts/ensure-s3-bucket.py .env

# Docker 形态：在 host 容器里跑，直接用容器环境里 compose 注入的 endpoint
# （compose 内网 http://seaweedfs:8333）与凭据；镜像里没有 .env，脚本跳过加载
docker compose -f deploy/compose.host.yaml exec -T host python scripts/ensure-s3-bucket.py
```

Docker 形态也可以在宿主机上跑，但 `deploy/.env` 通常不含 endpoint（它由
compose 注入），必须用进程环境显式给出宿主机侧的发布地址，否则脚本会按
「endpoint 为空 = AWS 默认端点」去连 AWS：
`AGENT_LEGION_S3_ENDPOINT=http://127.0.0.1:8333 uv run python scripts/ensure-s3-bucket.py deploy/.env`
（`AGENT_LEGION_S3_BIND` 改过时换成对应地址）。

**prod 页面的 CORS origin**：脚本只放行前端 dev server 的 origin
（`5173` / `5174` / `DEV_FRONTEND_PORT` 的 `127.0.0.1` 与 `localhost`
两种写法）。prod 前端由 Host 在 `:8000` 提供，浏览器直传的 origin 是用户
实际访问 Host 的地址（如 `http://192.0.2.1:8000`），需要额外追加一条规则
（与已有规则合并，重跑 `ensure-s3-bucket.py` 不会删掉它）。`put_bucket_cors`
是整份替换，所以脚本只在读到现有规则、或明确得到 `NoSuchCORSConfiguration`
（bucket 尚无 CORS）时才写；其它读失败直接报错退出，不写入：

```bash
# Docker 形态；原生形态把第一行换成
#   ORIGIN=http://<部署机地址>:8000 PYTHONPATH=. uv run --env-file .env python - <<'EOF'
docker compose -f deploy/compose.host.yaml exec -T -e ORIGIN=http://<部署机地址>:8000 host python - <<'EOF'
import os, boto3
from botocore.exceptions import ClientError
from server.app.storage import load_s3_settings
s = load_s3_settings()
c = boto3.client("s3", region_name=s.region, endpoint_url=s.endpoint_url or None,
                 aws_access_key_id=s.access_key or None, aws_secret_access_key=s.secret_key or None)
origin = os.environ["ORIGIN"]
try:
    rules = c.get_bucket_cors(Bucket=s.bucket)["CORSRules"]
except ClientError as exc:
    # 只有「bucket 还没有 CORS 配置」可以当空规则继续；凭据 / 权限 / bucket
    # 不存在 / 限流等读失败一律中止——否则下面的 put 会用只含新 origin 的
    # 规则整体覆盖掉现有 CORS（含其它生产 origin）
    if exc.response.get("Error", {}).get("Code") != "NoSuchCORSConfiguration":
        raise
    rules = []
if not any(origin in r.get("AllowedOrigins", []) for r in rules):
    rules.append({"AllowedOrigins": [origin], "AllowedMethods": ["PUT", "GET", "HEAD"],
                  "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600})
    c.put_bucket_cors(Bucket=s.bucket, CORSConfiguration={"CORSRules": rules})
print([r["AllowedOrigins"] for r in rules])
EOF
```

### 3.3 启动后检查

- 后端启动时在 PostgreSQL advisory 迁移锁下自动执行 schema 迁移；当前版本
  以 `server/app/db/schema.py` 的 `SCHEMA_VERSION` 为准（目前 v104）——该数字由
  `scripts/architecture/docs_consistency.py` 与代码对账，迁移链在
  `server/app/db/migration_chain.py`（及其 `migration_chain_recent.py` /
  `migration_chain_latest.py`）。
  升级前先备份数据库，见 [postgresql-runbook.md](postgresql-runbook.md)。v93 会把
  Agent 定义回填进 agent 节点执行档案，升级后核对回填报告，见
  [remote-execution-runbook.md §6.2](remote-execution-runbook.md#62-agent-profile-backfill-schema-v93-and-rolling-back-to-0716-935)。
- bundle 条目（文件夹整体一个条目）复用同一 bucket 与材料缓存，无额外
  存储配置。
- 上传一个文件验证闭环：`POST /api/workspaces/{id}/materials/presign`
  → PUT → `complete`，材料状态变 `ready`。
- 恢复 workspace 调度（后端每次启动都会把全部 scope 重置为 paused，
  `server/app/worker_control.py`）：经控制台恢复；`scripts/resume-workspaces.sh`
  只适用于原生 / dev 形态（它按根 `.env` 的 `AGENT_LEGION_DATABASE_URL` 直连
  数据库，Docker 形态的 PostgreSQL 不向宿主机发布端口），Docker 部署走控制台。
- worker 的 `claim_enabled` 默认关闭，打开方式见
  [agent-worker-deployment.md §5](agent-worker-deployment.md#5-启动-worker-机器上的-worker)。

## 4. 运维

- **TTL**：材料过期由实例设置 `materials_ttl_days`（admin 全局设置 →
  实例设置，或 `PUT /api/admin/instance-settings`）治理，非负整数天，
  默认 `0` = 关闭。开启后：`complete` 标记 `ready` 时按当前 TTL 写
  `materials.expires_at`（改设置即时生效，无需重启）；TTL sweeper
  （`sweeper_enabled` 单副本后台线程）把到期行翻成 `expired`——新引用
  在 run 创建与 dispatch 物化处都被拒（解析链只接受 `ready`），已在
  引用中的 job 不强行失效；`expired` 超过短暂 grace（10 分钟）且
  引用计数为 0 时物理删除（先删 S3 对象再删行，对象删除失败留到下轮
  重试）。孤儿对象兜底分前缀治理（`#340`）：产物在 `jobs/` 前缀
  下，Worker 直传的暂存对象在 `jobs-staging/` 前缀下（Host 核验后服务端
  copy 提升到 `jobs/` 权威 key；暂存源只在 finish 提交后由完成方删除，
  失败/冲突残留由 GC/lifecycle 兜底），三条前缀
  分开配规则——材料侧按你们对上传内容的数据分级策略设保留期（务必
  显著长于 `materials_ttl_days`，让 DB 侧先完成引用检查），`jobs/`
  前缀按产物保留策略另设，`jobs-staging/` 配短保留（如 1 天，孤儿
  暂存对象只是失败残留）。材料 key 在 bucket 根
  （`{workspace_id}/{hash|material_id}/v2g{上传代次}/m_{material_id}/{filename}`，
  代次段自 #1267 起（旧形态 `g{代次}`）、v2g 版本标记 + m_ 标记唯一段
  自 #1279 起、存量行可缺），与两个 jobs 前缀平级。注意 promote 回滚备份（key 含 `/.rollback/`
  段）也在该前缀下：promote 恢复最终失败时被刻意保留的备份是幸存清单
  行所指向旧字节的最后恢复源（`s3_jobs_gc` 对它们豁免回收，codex #774
  P1 族；豁免仅适用于 `jobs/` 与 `jobs-staging/` 前缀的 key——材料文件
  名允许含合法 `.rollback` 段，材料根前缀 key 不继承该豁免，#1285）——bucket lifecycle 规则只能按前缀过滤、无法按子串豁免，配
  `jobs-staging/` 短保留即接受「恢复处置死线 = 保留天数」。手工清理可用
  console（rustfs `:9001`；seaweedfs 为 master UI `:9333`）。
  - `jobs/` 与 `jobs-staging/` 的孤儿对象（行已删但删除失败/未执行、
    promote 中途失败的结果报告丢失残留）与材料根前缀的孤儿对象（已上传
    后重试留下的上一代 `g{代次}` key、行已删但对象删除失败的残留，
    #1269）由 `scripts/gc-s3-jobs.py` 治理：按前缀列举（材料为根前缀
    排除两个 jobs 前缀）对照 `job_artifacts` ∪ `materials` 清单行，
    宽限窗（默认 24h，`--grace-hours`/`--staging-grace-hours`
    可调）外的无行对象与过期暂存对象判定为孤儿；默认 dry-run，加
    `--apply` 执行删除，幂等可重跑。**材料删除批（#1314）**：材料根前缀
    孤儿只有同时过三道门才删除——①单次使用 key 文法（`v2g{代次}` +
    `m_{material_id}`，#1279，`s3_jobs_gc.material_key_single_use`）；
    ②对象年龄过 `max(--grace-hours, 1h)`（1h 为材料年龄下限
    `MATERIAL_MIN_GRACE_HOURS`，`--grace-hours 0` 也压不穿，删除前按当时
    时钟再复核）；③删除前逐批新鲜反查 materials ∪ job_artifacts 无行。
    承重墙是写端「行先于对象」协议：presign/重试/repair 在行锁内派生 key
    并提交后才签 URL；inline 文本与 demo seed 经
    `material_object_publish` 先落无 hash 的 uploading intent 行、再 PUT、
    再原子换成 ready 行——任何对象从第一个字节起都被一条行引用，反查
    「无行」即此后永不再被引用（id 不复用、代次单调）。年龄下限是纵深：
    兜住仍「先 PUT 后建行」的 #1312 期旧 Host（秒级窗口）与时钟偏差。
    旧文法材料孤儿（`g{代次}`、legacy 无代次段、`inline-{uuid}` 旧形态——
    key 可复用，check-then-delete 关不死窗口）仍**只报告不删除**（#1282
    收口纪律），回收靠 bucket lifecycle 规则兜底。输出分三栏：进删除批 /
    未满年龄下限暂缓 / 旧文法只报告；`--apply` 另报「材料删除门禁扣下」
    与「反查救下」的对象数。写端进程在 PUT 与发布之间崩溃会留下 uploading
    intent 行（对象受保护、不会被 GC），与浏览器放弃上传的 uploading 行
    同类，可在材料列表删除。逐格交错决策表见
    architecture/materials-and-runs-design.md §6.4.2。
    同理，workspace id 字面为 `jobs` / `jobs-staging` 时其材料 key 落在
    jobs 前缀扫描内，脚本在组装删除批前按材料形态文法（第 3 段为
    `(v2)g{代次}`；真 job 产物同位置是恒含下划线的 job id，零误伤）把这类
    key 分流进材料孤儿集合，同走上述材料删除门禁（#1282 r4 P1）。
    job authority key 侧（#1309 判定，排除出 #1279 协议面）：#853 起产物
    每次写落 per-attempt uuid4 版本 key，不可复用已构造性成立；#853 前
    legacy 固定 key 的 revalidate→delete 亚秒窗残留由 #1309 独立跟踪。
  - ETag 失配的恢复面（#1279）：消费侧 If-Match 检出 412 后，行级修复走
    `uv run python scripts/request_material_reupload.py <workspace_id>
    <material_id>`——把 ready 行翻回 uploading（代次 +1、轮换 v2g key、
    清 object_etag）并为**原行**新 key 签发受代次保护的一次性 PUT URL
    （hash 与无 hash 行统一由原 material_id 愈合：无 hash 行没有 presign
    按 hash 找旧行的回路）；用该 URL 重传后 complete 携带输出里的代次
    即恢复 ready，历史 job 引用随之消费新对象。重传 URL 同样签入
    `If-None-Match: *`（#1321）：手工 curl 重传必须带 `-H 'If-None-Match: *'`
    （缺头 403 SignatureDoesNotMatch），412 = 对象已存在按已上传处理。
    旧对象成孤儿（单次使用
    key），由 `scripts/gc-s3-jobs.py` 的材料删除批回收（#1314）。S3 快照恢复（aws s3 cp 重传会改变大行
    ETag）后、启动调度前必须跑 `scripts/rebind_material_etags.py`
    （先 dry-run 再 --apply；字节核验不过的行 fail-closed 只报告，退出
    码 1），步骤与纪律见 backup-restore-runbook.md 的对象存储恢复段。
    协议门禁（codex r7 / #1316）：消费侧 ETag 核验要求 Worker 协议 v6——
    滚动升级窗口内（Host 先升），无 hash 且 etag 已绑的材料任务、以及
    repair 进行中（uploading）的无 hash 材料任务对旧 Worker 不可领（留
    队列等升级，不耗 attempt）；hash 行、etag='' 行、failed/expired 行
    不门禁（后两者维持既有失败语义）。准入后、描述符组装前行被 repair
    重传或绑定的竞态，由 claim 响应按同一行快照复判
    （material_claim_descriptors.assert_worker_may_consume；拒绝 → 500 →
    lease 过期回收重排，消耗一次 attempt）。升级顺序与矩阵见
    remote-execution-runbook.md §5。
  - **存量行 ETag 绑定**（#1316）：v98 之前完成的无 hash 行
    `object_etag=''`（无内容身份）。新 Host **每次启动**都会起一个后台
    线程，按当前 HEAD 为它们一次性绑定 ETag，只要配置了对象存储就会运行，
    **不受** `sweeper_enabled`、`code_capacity`、claim 开关或 workspace
    暂停影响；候选来自 DB，重启不丢进度。绑定时同一条 CAS 把
    `upload_generation` +1（代次即身份，无 hash 行的缓存地址随之轮换，
    绑定前写入的 Host / Worker 缓存不再命中，codex r1 P1）。绑定之后与
    新上传的行同一语义：Host 与 v6 Worker 带 If-Match，升级前签出、仍未
    过期的 PUT URL 再被用来同尺寸覆盖时会 412 响亮失败（用上面的 repair
    流程愈合）。旧 Worker（< v6）对已绑 ETag 的无 hash 行**永久门禁**
    （任务保持 queued 直至 Worker 升级，与 0.8.0 对 complete 绑定行的
    语义一致）：旧 Worker 命中升级前裸 material_id 地址的缓存时不发
    GET，任何排空期都不能证明其缓存字节匹配已绑 ETag（codex r2 P1）。
    - 绑定失败（对象缺失、size 与行不符、存储不回 ETag、HEAD 报错）只
      报告不改行：该行保持升级前的 size-only 语义，不挂起、不翻 failed。
      HEAD 报错时线程每 10 分钟重扫、至多 3 轮；之后（或 DB 恢复后不想
      重启 Host 时）手工补跑：
      `uv run python scripts/bind_legacy_material_etags.py`（dry-run）→
      `... --apply`（幂等；库未被新 Host 迁移到当前版本时拒绝运行，
      本工具不触发迁移；退出码 1 = 仍有无法绑定的行）。
    - 残留（已声明，与 base 等价）：升级前、以及从 Host 启动到线程扫到
      该行之间（秒级，启动时 workspace 全部处于暂停）发生的同尺寸覆盖
      会被绑成身份，无锚可分辨。前提：停机升级、单副本 Host（新旧 Host
      不得同时在役签发 URL）；回滚到 v98 前代码不受支持（见
      backup-restore-runbook.md §2.3）。
  - 孤儿派生 bucket（`agent-legion-<worktree>` 命名、对应 worktree 已
    不存在的测试 bucket）由 `scripts/report-orphan-s3-buckets.py`
    报告（只报不删，逐个给出 `clean-worktree.sh` 收尾命令）。
  - bucket lifecycle（ILM）规则在 RustFS（alpha）上的支持未验证，
    逃生舱形态不依赖；上述脚本即仓库内的前缀级兜底。
- **缓存**：`data/materials_cache/` 是可淘汰缓存，可随时清空（下次
  dispatch 重新下载）；worker 侧在 `{work_root}/materials_cache`。
- **本地后端选择与切换**（#340）：`AGENT_LEGION_LOCAL_S3_BACKEND=
  seaweedfs|rustfs`（默认 seaweedfs）。切回 rustfs 逃生舱：在
  `deploy/.env`（docker 形态）或根 `.env`（dev/原生形态）同时覆盖
  `AGENT_LEGION_LOCAL_S3_BACKEND=rustfs`、
  `AGENT_LEGION_S3_ENDPOINT=http://rustfs:9000`、
  `AGENT_LEGION_S3_PUBLIC_ENDPOINT=http://127.0.0.1:9000` 三样；decide
  脚本对「endpoint 指向 rustfs 而未显式配 BACKEND」的存量配置会打提示。
  两个后端不要同时启用：端口不同（8333/9000）不冲突，但双写会分裂数据。
  SeaweedFS 的 master UI 在 `:9333`（对齐 rustfs console `:9001` 的
  暴露习惯），健康端点 `:8333/healthz`（匿名 200；根路径 403 是 S3 API
  对匿名请求的正常行为，同 rustfs 教训，不作探针）。
- **迁移后端**（本地后端互迁或迁外部 S3）：改 `deploy/.env` 的
  endpoint/凭据（`AGENT_LEGION_LOCAL_S3=auto` 会据此自动启停本地后端），
  数据用 `aws s3 sync s3://old s3://new` 或 `rclone`
  搬迁（目标 bucket 须为空，或先加 `--dryrun` 核对：sync 会用源对象覆盖
  目标里的同名对象；不要加 `--delete`）；`materials.storage_key` 与后端无关，无需改库。
- **demo 材料播种**：S3 配好后，新建/绑定 demo workspace 时自动播种
  `examples/` 演示材料；`make import-demo` 同样触发。

## 5. 故障排查

| 症状 | 排查 |
|---|---|
| 材料 API 503 | `AGENT_LEGION_S3_BUCKET` 未配置或服务启动时 env 未生效 |
| 上传 PUT 失败（浏览器） | presigned URL 的 host 是否浏览器可达（`AGENT_LEGION_S3_PUBLIC_ENDPOINT` 未配或配错时 URL 会指向 compose 内部地址）；bucket CORS 是否放行前端 origin；presigned URL 过期（1h）。412 = 条件写生效（对象已存在），网络重试场景按已上传处理即可；403 且 URL 的 SignedHeaders 含 `if-none-match` = 客户端 PUT 缺 `If-None-Match: *` 头（#1321） |
| complete 422 | 实际上传字节与声明 size/hash 不符，重新上传 |
| 节点报 "material storage is not configured" | Host/Worker 侧 env 缺失；Worker 路径靠 Host 签发的 presigned GET（1h 有效），失败会由 sweeper 重排队换新 URL |
| 缓存目录膨胀 | 调低 `AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES` 或手动清空 |
| PutObject 全量 503（master 日志 no free volumes），磁盘未写满 | SeaweedFS 可写槽位耗尽，见下节「可写槽位耗尽」 |

## 6. 可写槽位耗尽（SeaweedFS，PutObject 503 / no free volumes）

**机制**：SeaweedFS 的 volume 按 collection 成批预分配（每个 collection
一次创建一批 volume，默认 7 个）。S3 bucket `<b>` 的对象一律写进同名
collection `<b>`（每个派生 worktree bucket 首次写入就占一批槽位）；删除
bucket 会连带删其 collection 的 volume，只有 master 侧删除失败/超时才留
孤儿——`scripts/clean-worktree.sh` 在 bucket 删除后（或 bucket 已不存在
的重跑路径上）经 master `/vol/status` 只读核对同名 collection，残留即告警
并打印手动回收命令（`/col/delete`），确认没有同名 worktree 正在复用该
bucket 后由人执行；脚本刻意不自动删除，避免删到被重建复用的同名
collection（#824；master 地址默认由 `:8333` endpoint 推导
为同主机 `:9333`，可用 `AGENT_LEGION_SEAWEEDFS_MASTER_URL` 覆盖，非
seaweedfs 后端自动跳过）。**无 collection 标记（`""`）的 volume 不是孤儿**：
filer 的元数据变更日志（`/topics/.system/log`）落在这里，持续有小量写入，
遍历 `/buckets` 找不到引用属正常，不要 `volume.delete` 它们（会丢失元数据
变更历史）。另外**从未被写入过的空
volume 不在 `volume.deleteEmpty` 的回收范围**（回收条件要求 volume 有
过写入），长期增删 bucket 与空 collection 的预分配仍会攒下一批全空的
volume。另外 compose 曾以 `-volume.max=0`（按磁盘余量自动推导上限）运行：当
volume server 在 master 侧的注册信息 stale 时，自动推导会把可写槽位判成
0，master 认为没有可分配 volume，全部 PutObject 返回 503
（no free volumes）——表象是「磁盘远未写满却写满」。即便注册信息正常，
自动推导的上限 ≈ 磁盘余量 ÷ 2GiB，在余量几十 GB 的开发机上只有十几个
槽位：元数据日志的 `""`、prod 主 bucket、develop bucket 各占一批 7 个，
再有一两个活跃 worktree bucket 写入就会撞到 `only 0 volumes left`
（#824 实测）。现 compose 已改为
显式上限（`AGENT_LEGION_SEAWEEDFS_VOLUME_MAX`，默认 100；惰性增长的
上限闸门，volume 按需创建、不预占磁盘，100 × 2GiB ≈ 200GiB 的可增长
容量），但空 volume 堆积与未收尾 worktree 的 bucket 仍会挤占这个槽位
上限。注意上限只在容器**重建**时生效：仓库改了 compose 而容器沿用旧
命令行（`docker inspect` 看 `-volume.max`）即仍是旧行为。

**上限可调**：上限只是槽位闸门，调大**无需迁移数据**（新 volume 惰性
创建），在 `deploy/.env` 设 `AGENT_LEGION_SEAWEEDFS_VOLUME_MAX` 后
`docker compose -f deploy/compose.host.yaml up -d seaweedfs` 生效
（compose 文件非默认文件名，`-f` 不可省；或走 `make prod-up` 入口）。
升级本变更或调大上限前，先用
`weed shell` 确认当前 volume 数低于新上限：

```bash
docker exec <seaweedfs 容器> sh -c \
  'printf "lock\nvolume.list\nunlock\n" | weed shell -master=localhost:9333'
```

若新上限低于现有 volume 数（例如升级前部署已超过 100 个非空 volume，
或数据已增长到 100 × 2GiB ≈ 200GiB 量级），master 会停止分配新
volume，所有新写入返回 no free volumes——上限必须始终大于当前 volume
数。`volume.deleteEmpty` 对非空 volume 无效，不能把「超上限」状态救回。

**水位观察（日常运维项，#746）**：volume 数随 bucket/collection 增长单调
累积，删数据不会减少 volume 个数，打满只是时间问题——一旦打满，新
bucket（新实例、新派生 worktree）写不进去；已有 bucket 仅在其已有可写
volume 尚有空间时仍可写入，需要再分配 volume 时同样失败。只有部分 bucket
报错时容易被误判为个别环境故障；已有 bucket 写入失败时也不能据此排除卷上限
问题。把上面的 `volume.list`（或 master UI 的 volume 总数）列入例行巡检：
对比「已有 volume 数」与「当前生效的 `-volume.max`」
（`docker inspect` 看容器实际命令行，而不是只看 compose 文件），已用接近
上限（例如过半后增长明显、或剩余槽位不足几批 collection 预分配）就提前
调大 `AGENT_LEGION_SEAWEEDFS_VOLUME_MAX` 并重建容器——接近上限前扩容只是
改一个值，打满后才处理就是写入路径整体报错的救火。上限务必通过
`deploy/.env` 持久化，不要只靠临时 compose override：不带 override 重建
容器即回到旧上限。

**排查**：master UI（`:9333`）看 volume 总数与已用比例；PutObject 503 且
master 日志出现 `no free volumes` 即命中本问题。

**恢复**（先重启让 volume server 重新注册，再回收空 volume）：

```bash
docker restart <seaweedfs 容器>
# 先不带 -apply 预览（simulation mode，只列出将被删除的 volume），确认都是
# 预期的空 volume 后再执行带 -apply 的同一条命令
docker exec <seaweedfs 容器> sh -c \
  'printf "lock\nvolume.deleteEmpty -quietFor=1h\nunlock\n" | weed shell -master=localhost:9333'
docker exec <seaweedfs 容器> sh -c \
  'printf "lock\nvolume.deleteEmpty -quietFor=1h -apply\nunlock\n" | weed shell -master=localhost:9333'
```

`volume.deleteEmpty` 只删「空、有过写入、且静默超过 quietFor 时长」的
volume，幂等可重跑；删完 PutObject 即恢复。从未被写入过的空 volume 不在
其回收范围——若 volume 数仍贴着上限，再调大
`AGENT_LEGION_SEAWEEDFS_VOLUME_MAX`（见上节「上限可调」）
或清理无用 bucket/collection：已不需要的 worktree 走
`scripts/clean-worktree.sh <worktree名>`（bucket 删除通常连带回收同名
collection 卷；bucket 已删但卷残留时重跑同一命令会列出残留并给出手动回收
命令，`report-orphan-s3-buckets.py`
列出待收尾的孤儿派生 bucket）。
