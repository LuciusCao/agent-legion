# 材料存储（RustFS / S3）部署与运维

Agent Legion 的材料（用户上传文件）与后续的 job 产物统一存放在 S3 兼容
对象存储中。默认自托管 [RustFS](https://rustfs.com/)（Apache 2.0，MinIO
替代品），代码只对 S3 API 编程，可平行切换 Amazon S3 / MinIO / Garage。
设计背景见
[docs/architecture/materials-and-runs-design.md](architecture/materials-and-runs-design.md)。

## 1. 组件与配置面

| 项 | 值 | 说明 |
|---|---|---|
| 服务 | compose `rustfs`（`deploy/compose.host.yaml`） | S3 API `:9000`，Web console `:9001`，数据卷 `rustfs-data` |
| `AGENT_LEGION_S3_ENDPOINT` | `http://rustfs:9000`（compose 内自动注入） | 后端直连地址；留空 = AWS S3 |
| `AGENT_LEGION_S3_PUBLIC_ENDPOINT` | compose 默认 `http://127.0.0.1:9000` | presigned URL 的签发地址，必须浏览器 / remote worker 可达；留空则回落用内部 endpoint 签发 |
| `AGENT_LEGION_S3_BUCKET` | 默认 `agent-legion` | 每个部署实例一个 bucket；dev worktree 派生 `agent-legion-<worktree>` |
| `AGENT_LEGION_S3_ACCESS_KEY` / `AGENT_LEGION_S3_SECRET_KEY` | **必填，无默认值** | compose 只做 `${}` 字面插值，`deploy/.env` 必须写字面值；`_FILE` 变体仅原生形态可用。compose 同时把它注入 rustfs 容器作为其 root 凭据 |
| `AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES` | 默认 50GiB | 节点物化缓存（`data/materials_cache/`）容量上限，LRU 淘汰 |

凭据是实例级 infra 配置（与 `database.url` 同级），env-only 注入，不落
tracked yaml、DB、API 或日志（MATERIAL-SECRET-001）。

未配置 `AGENT_LEGION_S3_BUCKET` 时服务整体照常启动，只有 materials/runs
上传相关 API 返回 503（优雅降级）；但 **Docker 形态下 compose 因
`${AGENT_LEGION_S3_ACCESS_KEY:?}` 必填占位直接拒绝启动**——部署前必须
先配好 `deploy/.env`。

## 2. 首次部署 / 升级启用步骤

### 2.1 准备凭据与配置

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
变量写进 prod worktree 根的 `.env`——原生加载支持 `_FILE` 变体。
RustFS 容器不用手工起：`native-prod-up.sh` 会自动
`docker compose -f deploy/compose.host.yaml up -d rustfs`（幂等；docker
不可用或启动失败仅告警，材料 API 降级为 503，其余功能不受影响）。
原生形态的 `AGENT_LEGION_S3_ENDPOINT` 指向 `http://127.0.0.1:9000`，
`AGENT_LEGION_S3_PUBLIC_ENDPOINT` 指向浏览器 / remote worker 可达的地址。）

### 2.2 启动与建 bucket

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

### 2.3 启动后检查

- 后端启动时自动执行 schema 迁移（`job_batches` → `runs`，旧 payload
  解析下沉到 jobs）。**生产库有 26 万+ jobs，迁移 UPDATE 可能耗时数
  分钟，务必先备份数据库并在低峰执行**；迁移幂等可重入，中断后重启
  会继续。
- 上传一个文件验证闭环：`POST /api/workspaces/{id}/materials/presign`
  → PUT → `complete`，材料状态变 `ready`。
- 恢复 workspace 调度（后端每次启动都会把全部 scope 重置为 paused，
  `server/app/worker_control.py`）：经控制台或 init 脚本恢复。
- worker 的 `claim_enabled` 默认关闭，经 worker 控制台或
  `PUT /api/config` 打开。

## 3. 运维

- **TTL**：材料过期由 bucket lifecycle 规则 + `materials.expires_at`
  治理（治理机制随产物上云切片落地）；手工清理可用 console（`:9001`）。
- **缓存**：`data/materials_cache/` 是可淘汰缓存，可随时清空（下次
  dispatch 重新下载）；worker 侧在 `{work_root}/materials_cache`。
- **迁移后端**（RustFS → AWS S3 或反向）：改 `deploy/.env` 的
  endpoint/凭据，数据用 `aws s3 sync s3://old s3://new` 或 `rclone`
  搬迁；`materials.storage_key` 与后端无关，无需改库。
- **demo 材料播种**：S3 配好后，新建/绑定 demo workspace 时自动播种
  `examples/` 演示材料；`make import-demo` 同样触发。

## 4. 故障排查

| 症状 | 排查 |
|---|---|
| 材料 API 503 | `AGENT_LEGION_S3_BUCKET` 未配置或服务启动时 env 未生效 |
| 上传 PUT 失败（浏览器） | presigned URL 的 host 是否浏览器可达（`AGENT_LEGION_S3_PUBLIC_ENDPOINT` 未配或配错时 URL 会指向 compose 内部地址）；bucket CORS 是否放行前端 origin；presigned URL 过期（1h） |
| complete 422 | 实际上传字节与声明 size/hash 不符，重新上传 |
| 节点报 "material storage is not configured" | Host/Worker 侧 env 缺失；Worker 路径靠 Host 签发的 presigned GET（1h 有效），失败会由 sweeper 重排队换新 URL |
| 缓存目录膨胀 | 调低 `AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES` 或手动清空 |
