"""Worker 的 compose / Dockerfile 部署契约（控制面发布插值、状态卷分离、
#489 宿主侧发布地址注入与 worker-ctrl 网络隔离）。从
test_agent_worker_service.py 按主题拆出（原文件超 800 行，用例零改动迁移）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_keeps_control_api_local_and_state_separate_from_executions() -> None:
    standalone = (ROOT / "deploy/compose.worker.yaml").read_text(encoding="utf-8")
    host = (ROOT / "deploy/compose.host.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    for compose in (standalone, host):
        assert "${AGENT_WORKER_UI_BIND:-127.0.0.1}:8787:8787" in compose
        assert "worker-control:/var/lib/agent-legion-worker-control" in compose
        assert "worker-data:/var/lib/agent-legion-worker" in compose
        assert "${VELITES_PROVIDER_ENV_FILE:-./velites-provider.env}" in compose
        assert "required: false" in compose
    assert "deploy/velites-provider.env" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "COPY shared /app/shared" in dockerfile
    assert 'python3 -c "import worker.service' in dockerfile
    assert "worker/cli_args.py /usr/local/bin/agent_worker_cli_args.py" in dockerfile


def test_compose_files_publish_effective_bind_to_worker_service() -> None:
    """#489：容器内必绑 0.0.0.0，宿主侧发布地址须同步传给 service。

    AGENT_WORKER_UI_EFFECTIVE_BIND 与 ports 发布行同一 ${AGENT_WORKER_UI_BIND}
    插值源（.env 一处改、两处同步），service 据此判定 token 是否内嵌。
    三个 compose 的 worker 服务都要带——standalone 是一键安装的拉取目标，
    漏一处即该形态退化回「永不内嵌」的旧判定（读文件断言钉住双/三文件
    同步的先例见 test_compose_files_carry_velites_mount_and_guard）。
    """
    for name in (
        "deploy/compose.worker.yaml",
        "deploy/compose.worker.standalone.yaml",
        "deploy/compose.host.yaml",
    ):
        compose = (ROOT / name).read_text(encoding="utf-8")
        # 与 ports 发布行同源：同一变量、同一默认值，用户改 .env 一处生效两处
        assert "AGENT_WORKER_UI_EFFECTIVE_BIND: ${AGENT_WORKER_UI_BIND:-127.0.0.1}" in compose, (
            f"{name} 缺宿主侧发布地址注入或插值与 ports 行不同源"
        )
        assert "${AGENT_WORKER_UI_BIND:-127.0.0.1}" in compose, f"{name} 缺 UI bind 发布插值行"


def test_compose_host_worker_network_isolated_from_peer_services() -> None:
    """#489 P1 网络契约：host compose 的 worker 不得与 postgres/seaweedfs/rustfs 共网。

    worker 控制台 GET / 无鉴权（token 内嵌页面的设计前提），同网 peer 容器
    即可 curl http://worker:8787/ 提取 control token 接管控制面——默认回环
    发布下 service 会内嵌 token（判定只看宿主侧发布面），挡不住 compose
    内网。断言以解析 YAML 求网络集合交集的方式钉住（字符串包含式断言钉不
    住「共享隐式 default」的缺省形态——不写 networks 键时五个服务在文本上
    完全一致）。
    """
    doc = yaml.safe_load((ROOT / "deploy/compose.host.yaml").read_text(encoding="utf-8"))
    services = doc["services"]

    def service_networks(name: str) -> set[str]:
        # compose 语义：服务未声明 networks 键时挂隐式 default 网络
        raw = services[name].get("networks") or ["default"]
        return set(raw) if isinstance(raw, list) else set(raw)

    worker_networks = service_networks("worker")
    assert worker_networks, "worker 未声明 networks：隐式 default 会让它与全部 peer 共网"
    for peer in ("postgres", "seaweedfs", "rustfs"):
        shared = worker_networks & service_networks(peer)
        assert not shared, (
            f"compose.host.yaml 的 worker 与 {peer} 共享网络 {sorted(shared)}：peer 容器"
            "可达无鉴权的 worker 控制台，默认回环发布下的 token 内嵌即向同网段"
            "泄漏 control token（issue #489 安全不变量）"
        )
    # worker 出站依赖的唯一 peer 是 host：隔离不得切断 host_url 通道
    assert worker_networks & service_networks("host"), (
        "worker 与 host 无共享网络：register/claim/heartbeat/result 将全部失联"
    )
    # host 双挂 default：postgres / 对象存储的既有依赖不受隔离影响
    assert "default" in service_networks("host")
