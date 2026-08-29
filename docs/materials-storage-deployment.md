# 材料存储（RustFS / S3）部署与运维

Agent Legion 的材料（用户上传文件）与后续的 job 产物统一存放在 S3 兼容
对象存储中。默认自托管 [RustFS](https://rustfs.com/)（Apache 2.0，MinIO
替代品），代码只对 S3 API 编程，可平行切换 Amazon S3 / MinIO / Garage。
设计背景见
[docs/architecture/materials-and-runs-design.md](architecture/materials-and-runs-design.md)。

## 1. 组件与配置面

| 项 | 值 | 说明 |
|---|---|---|
| 服务 | compose `rustfs`（`deploy/compose.host.yaml`，挂 `materials-local` profile） | S3 API `:9000`，Web console `:9001`，数据卷 `rustfs-data`；默认 `docker compose up` 不拉起，由 prod-up 入口按 `AGENT_LEGION_LOCAL_S3` 决策后加 `--profile materials-local` |
| `AGENT_LEGION_LOCAL_S3` | `auto`（默认）/ `always` / `never` | 本地 RustFS 三态开关，判断逻辑见 `scripts/local-s3-decide.sh`（所有 prod-up / stack 入口共用）。`auto`：endpoint 指向本机或未配置任何 S3 → 启动；endpoint 远程，或只配 bucket/凭据不配 endpoint（AWS 默认端点写法）→ 跳过并打一行原因日志 |
| `AGENT_LEGION_S3_ENDPOINT` | docker stack 默认注入 `http://rustfs:9000`（可在 `deploy/.env` 覆盖；显式空值 = AWS S3 默认端点） | 后端直连地址；远程地址会让 `auto` 跳过本地 rustfs |
| `AGENT_LEGION_S3_PUBLIC_ENDPOINT` | compose 默认 `http://127.0.0.1:9000` | presigned URL 的签发地址，必须浏览器 / remote worker 可达；留空则回落用内部 endpoint 签发 |
| `AGENT_LEGION_S3_BUCKET` | 默认 `agent-legion` | 每个部署实例一个 bucket；dev worktree 派生 `agent-legion-<worktree>` |
| `AGENT_LEGION_S3_ACCESS_KEY` / `AGENT_LEGION_S3_SECRET_KEY` | 本地 RustFS 形态必填；外部 S3 走默认凭据链时留空 | compose 只做 `${}` 字面插值，`deploy/.env` 必须写字面值；`_FILE` 变体仅原生形态可用。compose 同时把它注入 rustfs 容器作为其 root 凭据 |
| `AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES` | 默认 50GiB | 节点物化缓存（`data/materials_cache/`）容量上限，LRU 淘汰 |

凭据是实例级 infra 配置（与 `database.url` 同级），env-only 注入，不落
tracked yaml、DB、API 或日志（MATERIAL-SECRET-001）。

未配置 `AGENT_LEGION_S3_BUCKET` 时服务整体照常启动，只有 materials/runs
上传相关 API 返回 503（优雅降级）。**Docker 形态下决策为启动本地 RustFS
但凭据未配齐时，prod-up 入口（`scripts/local-s3-decide.sh`）fail-fast**
——RustFS 留空凭据会回落镜像默认的公开凭据，必须拦住；部署前先配好
`deploy/.env`。

## 2. 开发形态（make install / make dev-up）

开发路径与生产部署分开：全新 clone 后 `make install`（`scripts/install-deps.sh`，
幂等）一键装齐前置依赖并初始化项目——uv sync、建 `agent_legion_dev` 库、从
`.env.example` 生成 `.env` 并填入随机 `AGENT_LEGION_S3_ACCESS_KEY/SECRET_KEY`
（同时作为 rustfs 容器的 root 凭据；`.env` 已存在但凭据为空时会幂等补填）、
生成 `deploy/secrets/vault_master_key`、构建 velites 二进制、装前端依赖、
种子 `config/agent-worker.yaml`。

`make dev-up`（`scripts/dev_stack.sh`）启动开发进程前先经
`scripts/local-s3-decide.sh .env` 决策本地 RustFS（与 prod 入口同一套
`AGENT_LEGION_LOCAL_S3` 三态逻辑）：决策为 start 时从根 `.env` 显式 export
S3 凭据后 `docker compose -f deploy/compose.host.yaml up -d rustfs`——
**dev 形态只有根 `.env` 一份配置**（不读 `deploy/.env`），凭据经环境变量
注入 compose 插值，因此不存在「两处凭据不一致」的坑。**rustfs 容器已在
运行时 dev-up 跳过 recreate 直接确认 bucket**：compose 项目名固定
`agent-legion`，全机（prod 与所有 worktree）共享同一个容器，凭据不同的
`up -d` 会 recreate 容器并打断持旧凭据的一方；容器在但凭据不匹配会在建
bucket 处告警暴露。起完后调用
`scripts/ensure-s3-bucket.py` 确保 bucket 与浏览器直传 CORS 就绪
（与 `init-worktree.sh` 共用同一脚本）。docker 缺失、启动失败或建 bucket
失败都只告警不阻断：材料 API 降级为 503，其余功能不受影响，就绪后重跑
`make dev-up` 可补齐存储；若期间跳过了 demo 示例材料播种，需另跑一次
`make import-demo`（幂等）补播种——`make dev-up` 本身不会重播材料。
`make dev-status` 会顺带显示 rustfs 容器状态。

开发环境切外部对象存储：改根 `.env` 的 `AGENT_LEGION_S3_ENDPOINT` /
凭据 / `AGENT_LEGION_S3_BUCKET` 三样即可（AWS 默认端点写法是显式留空
endpoint），`auto` 决策会自动跳过本地 rustfs；细节同 §3.1.1。

## 3. 首次部署 / 升级启用步骤

### 3.1 准备凭据与配置

compose 对 host 与 rustfs 都是 `${AGENT_LEGION_S3_ACCESS_KEY}` 字面插值，
**不支持 `_FILE` 变体**（写了会把路径字符串当 access key 注入）。凭据
直接以字面值写进 `deploy/.env`（该文件已被 git/docker 忽略，与
`LLM_GATEWAY_TOKEN` 同一通道）：

```bash
cd <prod worktree>
umask 077
cat >> deploy/.env <<EOF
AGENT_LEGION_S3_BUCKET=agent-legion
AGENT_LEGION_S3_ACCESS_KEY=$(openssl rand -hex 20)
AGENT_LEGION_S3_SECRET_KEY=$(openssl rand -hex 40)
EOF
chmod 600 deploy/.env
```

浏览器 / remote worker 从宿主机外访问时，再把 presigned URL 的签发地址
覆盖为可达地址（默认 `http://127.0.0.1:9000`，匹配 rustfs 端口映射）：

```bash
echo 'AGENT_LEGION_S3_PUBLIC_ENDPOINT=http://<宿主机地址>:9000' >> deploy/.env
```

（原生形态 `make prod-up` 的后端/worker 是本机进程，不经 compose：把同名
变量写进 prod worktree 根的 `.env`——原生加载支持 `_FILE` 变体；注意
compose 插值只读 `deploy/.env`，rustfs 容器的 root 凭据以 `deploy/.env`
为准，两处要写同一组值。RustFS 容器不用手工起：`native-prod-up.sh` 经
`scripts/local-s3-decide.sh` 决策后自动
`docker compose -f deploy/compose.host.yaml up -d rustfs`（幂等；docker
不可用或启动失败仅告警，材料 API 降级为 503，其余功能不受影响）。
原生形态的 `AGENT_LEGION_S3_ENDPOINT` 指向 `http://127.0.0.1:9000`，
`AGENT_LEGION_S3_PUBLIC_ENDPOINT` 指向浏览器 / remote worker 可达的地址。）

### 3.1.1 使用外部对象存储（AWS S3 / MinIO / Garage）

代码只对 S3 API 编程，外部存储只需改配置，不需要改代码：

1. 在 `deploy/.env`（docker 形态，compose 插值只读它）或根 `.env`
   （原生形态）配置外部存储：
   - 自建 S3 兼容服务：`AGENT_LEGION_S3_ENDPOINT=https://<外部地址>` +
     bucket + 凭据；
   - AWS S3：显式留空 endpoint（`AGENT_LEGION_S3_ENDPOINT=`，docker 形态
     空值会盖掉 compose 注入的 rustfs 默认值）+ bucket，凭据写静态
     key 或全留空走 boto3 默认凭据链（IAM role 等）；
   - `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 同步指向客户端可达地址（AWS 默认
     端点场景同样显式留空）。
2. 本地 RustFS 会随 `auto` 决策自动跳过（endpoint 远程或只配 bucket/凭据
   时 skip，并打一行原因日志）；也可用 `AGENT_LEGION_LOCAL_S3=never`
   显式关闭，`always` 则恢复旧版无条件启动。
3. auto 误判不会静默失败：后端启动自检（probe 的 DEGRADED 日志）与
   `/api/health` 的 `storage.reachable` 会暴露。

### 3.2 启动与建 bucket

```bash
git pull
make prod-up            # 或 make prod-up docker
```

首次启动 rustfs 后创建 bucket（一次性；dev 环境由 `init-worktree.sh`
自动完成）：

```bash
cd <repo root>
UV_CACHE_DIR=.uv-cache uv run python - <<'EOF'
import boto3
from botocore.exceptions import ClientError
from server.app.storage import load_s3_settings

s = load_s3_settings()  # 读 .env 中的 AGENT_LEGION_S3_*
kwargs = {"region_name": s.region}
if s.endpoint_url:
    kwargs["endpoint_url"] = s.endpoint_url
if s.access_key:
    kwargs.update(aws_access_key_id=s.access_key, aws_secret_access_key=s.secret_key)
client = boto3.client("s3", **kwargs)
try:
    client.head_bucket(Bucket=s.bucket)
    print("bucket 已存在")
except ClientError:
    client.create_bucket(Bucket=s.bucket)
    print(f"已创建 bucket: {s.bucket}")
# 浏览器直传要求 bucket CORS 放行前端 origin 的 PUT/GET 并暴露 ETag；
# AllowedOrigins 按实际前端地址调整（prod 页面与 rustfs 不同源）。
client.put_bucket_cors(
    Bucket=s.bucket,
    CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["http://127.0.0.1:8000"],
        "AllowedMethods": ["PUT", "GET", "HEAD"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }]},
)
print("已配置 bucket CORS")
EOF
```

（等价地也可用 `aws s3 mb s3://<bucket> --endpoint-url <rustfs地址>`。）

### 3.3 启动后检查

- 后端启动时自动执行 schema 迁移（`job_batches` → `runs`，旧 payload
  解析下沉到 jobs）。**存量 jobs 较多时迁移 UPDATE 可能耗时数分钟，
  务必先备份数据库并在低峰执行**；迁移幂等可重入，中断后重启
  会继续。
- 当前 schema 版本以 `server/app/db/schema.py` 的 `SCHEMA_VERSION` 为准
  （目前 v64）。近期迁移随启动自动执行：v54（`job_artifacts` 产物清单表）、
  v55（`material_bundles`）、v56（`job_node_status_counts` 触发器维护的
  状态计数）、v57（`studio_chat_sessions.draft_yaml`）、v58（scoped worker
  token——撤销存量全局 register token，行为变更）、v61（Studio workflow
  草稿表）、v62（workspace id 与 workflow key 绑定，存量 id 重命名）、
  v63（产物预览隐藏列表）、v64（workspace 级 Agent 默认配置三列退役
  drop）。v59（`jobs(run_id)` 索引）与 v60（register token ids 列）与本
  部署面无直接关系。
- bundle 条目（文件夹整体一个条目）复用同一 bucket 与材料缓存，无额外
  存储配置。
- 上传一个文件验证闭环：`POST /api/workspaces/{id}/materials/presign`
  → PUT → `complete`，材料状态变 `ready`。
- 恢复 workspace 调度（后端每次启动都会把全部 scope 重置为 paused，
  `server/app/worker_control.py`）：经控制台或 init 脚本恢复。
- worker 的 `claim_enabled` 默认关闭，经 worker 控制台或
  `PUT /api/config` 打开。

## 4. 运维

- **TTL**：材料过期由实例设置 `materials_ttl_days`（admin 全局设置 →
  实例设置，或 `PUT /api/admin/instance-settings`）治理，非负整数天，
  默认 `0` = 关闭。开启后：`complete` 标记 `ready` 时按当前 TTL 写
  `materials.expires_at`（改设置即时生效，无需重启）；TTL sweeper
  （`sweeper_enabled` 单副本后台线程）把到期行翻成 `expired`——新引用
  在 run 创建与 dispatch 物化处都被拒（解析链只接受 `ready`），已在
  引用中的 job 不强行失效；`expired` 超过短暂 grace（10 分钟）且
  引用计数为 0 时物理删除（先删 S3 对象再删行，对象删除失败留到下轮
  重试）。bucket lifecycle 规则是孤儿对象兜底：材料 key 在 bucket 根
  （`{workspace_id}/{content_hash}/{filename}`），产物在 `jobs/` 前缀
  下，Worker 直传的暂存对象在 `jobs-staging/` 前缀下（Host 核验后服务端
  copy 提升到 `jobs/` 权威 key 并 best-effort 删除暂存对象），三条前缀
  分开配规则——材料侧按你们对上传内容的数据分级策略设保留期（务必
  显著长于 `materials_ttl_days`，让 DB 侧先完成引用检查），`jobs/`
  前缀按产物保留策略另设，`jobs-staging/` 配短保留（如 1 天，孤儿
  暂存对象只是失败残留）。手工清理可用 console（`:9001`）。
- **缓存**：`data/materials_cache/` 是可淘汰缓存，可随时清空（下次
  dispatch 重新下载）；worker 侧在 `{work_root}/materials_cache`。
- **迁移后端**（RustFS → AWS S3 或反向）：改 `deploy/.env` 的
  endpoint/凭据（`AGENT_LEGION_LOCAL_S3=auto` 会据此自动启停本地
  rustfs），数据用 `aws s3 sync s3://old s3://new` 或 `rclone`
  搬迁；`materials.storage_key` 与后端无关，无需改库。
- **demo 材料播种**：S3 配好后，新建/绑定 demo workspace 时自动播种
  `examples/` 演示材料；`make import-demo` 同样触发。

## 5. 故障排查

| 症状 | 排查 |
|---|---|
| 材料 API 503 | `AGENT_LEGION_S3_BUCKET` 未配置或服务启动时 env 未生效 |
| 上传 PUT 失败（浏览器） | presigned URL 的 host 是否浏览器可达（`AGENT_LEGION_S3_PUBLIC_ENDPOINT` 未配或配错时 URL 会指向 compose 内部地址）；bucket CORS 是否放行前端 origin；presigned URL 过期（1h） |
| complete 422 | 实际上传字节与声明 size/hash 不符，重新上传 |
| 节点报 "material storage is not configured" | Host/Worker 侧 env 缺失；Worker 路径靠 Host 签发的 presigned GET（1h 有效），失败会由 sweeper 重排队换新 URL |
| 缓存目录膨胀 | 调低 `AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES` 或手动清空 |
