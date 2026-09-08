#!/usr/bin/env python3
"""水位补货式批量投放 CLI（#505 drip-feed submitter）。

给「几十万级 items 投进一个 workspace」的运营场景一个官方工具，替代
用户手写的「读水位 → 分批 POST → 失败重试」循环：

- items 清单文件（.jsonl 每行一个 item；.csv 逐行转 item——前者保序
  保字节、后者给 spreadsheet 运营留的口子：统一表头可混排三型行，空
  单元格按「字段缺省」处理、不进 item），item 契约与
  ``POST /workspaces/{id}/runs`` 完全一致（material/bundle/ref 三型）；
- 循环：读 workspace 非终态 job 水位（GET /workspaces/{id}/stats 的
  job_stats，v36 计数表触发器维护，PK 点查成本），低于水位线就 POST
  下一批（批大小 ≤ workflows.max_items_per_run），否则 sleep 可配间隔
  再查，直到清单投完；水位线是补货触发阈值而非容量承诺——低于批大小
  的低水位线 + 大批次是合法的突发节奏（投一批后水位涨过水位线，等回
  落再补），启动期不做「至少容纳一批」的拒绝；
- 认证：用户名/密码登录（session cookie + CSRF 头），与
  scripts/stress 的惯例一致，绝不硬编码凭据。

幂等性（本工具最重要的性质，issue #505 验收核心）：崩溃 / kill -9 /
重启后直接重跑同一个命令，零重复、零丢失。依据是提交侧的既有语义，
不在脚本里做任何本地去重状态：

- 跨批去重：run_service.create_run 的 dedup 过滤
  （server/app/services/run_service.py:136 起，点查
  JobQueries.filter_existing_dedup_keys）——items 的
  (source_type, source_id) 已有 job 的直接跳过，重发只补缺；
- 批内失败自愈：分块事务（#467 A3，create_jobs_bulk）中途失败时已提交
  chunk 保留、run 行标记 failed 带进度；重发同批走上面的 dedup 过滤
  自动跳过已建 job，run 行由 create_run 的确定性 run id upsert /
  heal_failed_run_if_duplicate（#501）治愈；
- 已成功批的重发：服务端对「全部 items 已有 job 且非 failed run 现场」
  的提交回 400 "No tasks were resolved"（run_service.py 的全重复分支，
  行为有服务端测试钉住）。工具识别该信号后把本批视作
  created_count=0 的成功——游标推进、继续下一批，不重试。
- run id 确定性：同 items 的重发解析到同一 run 行
  （run_healing.deterministic_run_id），不产生 run 垃圾。

批间失败重试：单批 POST 失败按 --retry-wait 线性退避（上限 60s）重试
--retry-max 次（默认无限——水位循环里它总会再轮到；重试同样吃 dedup
幂等）。只有 5xx 与网络错误进重试；401/403/422 是确定性失败，重试
无意义，直接报错退出。清单游标只在批次「成功被服务端接受（或被全
重复 400 吸收）」后前进。

用法:
    uv run python scripts/submit_campaign.py \
        --base http://127.0.0.1:8000 \
        --username admin --password '***' \
        --workspace-id <workspace-id> \
        --items campaign.jsonl \
        [--watermark 30000] [--batch-size 5000] [--poll-interval 10] \
        [--dry-run]

--password 缺省时读环境变量 AGENT_LEGION_CAMPAIGN_PASSWORD。

退出码: 0 完成（含 dry-run）；2 参数/清单/认证等使用错误；3 提交层
失败（重试耗尽或不可恢复的 4xx）。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

# 幂等口径的「非终态」：workspace stats 的 job_stats 只区分
# pending/running/completed/failed/paused/awaiting_approval（计数表里
# queued 折叠为 pending，见 JobStatusQueriesMixin.count_jobs_by_status）。
# #349 的容量红线「非终态 job 规模 ~5×10^4」针对的是调度/重扫负载，
# 其口径涵盖一切还没 settled 的 job——含 paused（占着非终态集合、还会
# 被恢复）与 awaiting_approval（等人放行，不消费执行槽但占重扫集合）。
# 工具侧取宽口径：total - completed - failed，与红线的集合定义一致，
# 避免投放方把 paused 积压误判为「还有水位余量」。
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed"})

# 水位线默认 3×10^4（issue #505 本机实测标定）：稳态调度 pass ~1s，距
# 15s slow-pass 警告（workflow_worker/thread.py note_pass 的 slow 阈值）
# 有量级余量；#349 红线 5×10^4 的运营上限之下留 provider 降速缓冲。
DEFAULT_WATERMARK = 30_000

# 批大小默认 5k：#467 分块化实测 5k items 提交 6.9s 有界返回；issue
# 建议批 5k–2×10^4（≤ workflows.max_items_per_run 默认 2×10^4）。
DEFAULT_BATCH_SIZE = 5_000

# 批间重试的线性退避上限（秒）：--retry-wait 是基数，attempt 线性放大，
# 但无限重试模式下不给 5xx 场景累积出刻钟级的单次等待。
MAX_RETRY_WAIT = 60.0

# --password 缺省时的取数环境变量（密码不进 shell history / ps）。
PASSWORD_ENV_VAR = "AGENT_LEGION_CAMPAIGN_PASSWORD"

# 「本批已被服务端完全吸收」的 400 信号串：run_service.create_run 对
# 全量重复（无 failed-run 治愈现场）的提交回 InvalidOperationError
# ("No tasks were resolved from input")，HTTP 层映射为 400
# detail（routes/job_http.py）。消息串有服务端测试钉住
# （test_run_service_chunking.py test_all_duplicate_*）；审核 #505 P1-1
# 认定这是零后端改动约束下识别「重跑已成功批」的唯一通路。
ABSORBED_BY_DEDUP_MARKER = "No tasks were resolved"

_VALID_ITEM_TYPES = frozenset({"material", "bundle", "ref"})
_ITEM_REQUIRED_FIELDS = {
    "material": ("material_id",),
    "bundle": ("bundle_id",),
    "ref": ("connection_key", "external_id"),
}


class UsageError(Exception):
    """使用错误（参数 / 清单 / 认证）——操作员可直接修正，退出码 2。"""


class SubmitError(Exception):
    """提交层失败（重试耗尽 / 不可恢复 4xx）——退出码 3。

    transient 属性标记该错误是否值得重试（5xx / 网络错误 / 未知形态）；
    确定性 4xx（401/403/422、非吸收语义的 400）不进重试循环。
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


# ---------------------------------------------------------------------------
# 纯 helpers（单测直测，不碰网络）
# ---------------------------------------------------------------------------


def normalize_item(raw: dict[str, Any], *, source: str) -> dict[str, Any]:
    """把清单行规整成 POST /runs 的 item 契约。

    ref 的 params 缺省补 ``{}``（契约默认值，见 RunItemRef）；其余字段
    原样透传（RunCreateRequest 是 extra="forbid"，多余字段服务端会 422，
    这里不做字段裁剪——提前报错属于服务端契约的职责，工具重复一遍反而
    会漂移）。CSV 来源额外做 str 化（csv 无类型）。
    """
    if not isinstance(raw, dict):
        raise UsageError(f"{source}: item 必须是 JSON object，收到 {type(raw).__name__}")
    item_type = raw.get("type")
    if item_type not in _VALID_ITEM_TYPES:
        raise UsageError(f"{source}: 不支持的 item type {item_type!r}（支持 {_VALID_ITEM_TYPES}）")
    item: dict[str, Any] = {}
    for key, value in raw.items():
        item[str(key)] = value.strip() if isinstance(value, str) else value
    missing = [
        field for field in _ITEM_REQUIRED_FIELDS[str(item_type)] if not str(item.get(field) or "")
    ]
    if missing:
        raise UsageError(f"{source}: {item_type} item 缺少必填字段 {missing}")
    if item_type == "ref" and "params" not in item:
        item["params"] = {}
    return item


def load_items(path: Path) -> list[dict[str, Any]]:
    """读清单文件（.jsonl 每行一个 item / .csv 逐行转 item），保序。"""
    if not path.is_file():
        raise UsageError(f"清单文件不存在: {path}")
    items: list[dict[str, Any]] = []
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as fh:
            for row_number, row in enumerate(csv.DictReader(fh), start=2):
                if all(value in (None, "") for value in row.values()):
                    continue
                # 混合表头（material/bundle/ref 共用一张表）时 DictReader 给
                # 每行附带其他类型的空列（material 行带 bundle_id=""）。CSV
                # 的空单元格只能表达「字段缺省」：POST /runs 契约（三个
                # RunItem 均 extra="forbid"、必填字段 min_length=1）对缺省
                # 字段走默认值（如 ref.params 补 {}），显式空串列必 422
                # （codex #531 P2-2）——进 normalize 前丢弃空值列（含短缺
                # 行尾列的 None）。
                cleaned = {key: value for key, value in row.items() if value not in (None, "")}
                items.append(normalize_item(cleaned, source=f"{path.name}:{row_number}"))
    else:
        with path.open(encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, start=1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise UsageError(f"{path.name}:{line_number}: JSON 解析失败: {exc}") from exc
                items.append(normalize_item(raw, source=f"{path.name}:{line_number}"))
    if not items:
        raise UsageError(f"清单文件没有可用 item: {path}")
    return items


def non_terminal_count(job_stats: dict[str, int]) -> int:
    """水位口径：job_stats 总数减终态（completed/failed）。

    宽口径对齐 #349 红线的集合定义（一切未 settled 的 job，含
    paused / awaiting_approval），见 TERMINAL_JOB_STATUSES 注释。
    """
    return sum(count for status, count in job_stats.items() if status not in TERMINAL_JOB_STATUSES)


def check_batch_size(batch_size: int, max_items_per_run: int) -> None:
    """批大小护栏：超 max_items_per_run 的批 POST 必 422（#358 护栏）。"""
    if batch_size < 1:
        raise UsageError(f"--batch-size 必须 >= 1，收到 {batch_size}")
    if max_items_per_run and batch_size > max_items_per_run:
        raise UsageError(
            f"--batch-size {batch_size} 超过 workflows.max_items_per_run="
            f"{max_items_per_run}（#358 护栏，超量 POST 会 422）；"
            "调大批大小前先调实例设置"
        )


def check_watermark(watermark: int) -> None:
    """水位线护栏：只拦非正数（触发阈值没有 0/负值语义）。

    水位线是「补货触发阈值」——水位低于它就投下一批——不是容量承诺
    （issue #505 语义）：低水位线 + 大批次是合法的突发配置（codex #531
    P2-1：--watermark=100 --batch-size=5000 且当前水位 0 时 0 < 100 照常
    投第一批，「水位低于批大小就永远补不进一批」不成立），启动期不做
    「至少容纳一批」的拒绝。
    """
    if watermark < 1:
        raise UsageError(f"--watermark 必须 >= 1，收到 {watermark}")


# ---------------------------------------------------------------------------
# HTTP 层（认证 + 水位读 + 批提交；单测用桩替注入）
# ---------------------------------------------------------------------------


def _response_detail(response: Any) -> str:
    try:
        return str(response.json().get("detail") or "")
    except Exception:  # #204 broad-except audit: detail 解析尽力而为，响应体不是 JSON 时回落原文
        return str(response.text or "")


def _raise_http_error(response: Any, url: str) -> None:
    status = int(response.status_code)
    detail = _response_detail(response)
    if status == 401:
        raise SubmitError(
            f"{url} -> HTTP 401: {detail[:500]}"
            "（未认证：长投放中 session 可能已过期，重跑同一命令幂等续投）"
        ) from None
    if status == 403:
        raise SubmitError(
            f"{url} -> HTTP 403: {detail[:500]}"
            "（无权限：检查账号角色 / workspace 成员资格；instance-settings 读取需要 admin）"
        ) from None
    if status == 422:
        raise SubmitError(
            f"{url} -> HTTP 422: {detail[:500]}"
            "（契约校验失败：检查 --batch-size 与 items 的字段契约，重试不会改变结果）"
        ) from None
    transient = status >= 500
    raise SubmitError(f"{url} -> HTTP {status}: {detail[:500]}", transient=transient) from None


class CampaignClient:
    """登录会话 + 水位读 + 批提交。requests.Session 依赖延迟导入并
    注入（默认环境 import requests；单测桩替 requests 模块）。"""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        session: Any,
        log: Callable[[str], None],
    ) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session
        self.log = log

    @classmethod
    def login(
        cls,
        base_url: str,
        username: str,
        password: str,
        timeout: float,
        requests_module: Any,
        log: Callable[[str], None],
    ) -> CampaignClient:
        session = requests_module.Session()
        # cookie 会话的写请求必须带 CSRF 头（auth.dependencies
        # get_current_user 的 cookie-channel 检查），先于登录设置无副作用。
        session.headers.update({"x-agent-legion-request": "1"})
        response = session.post(
            f"{base_url.rstrip('/')}/api/auth/login",
            json={"username": username, "password": password},
            timeout=timeout,
        )
        if response.status_code != 200:
            raise UsageError(
                f"登录失败: HTTP {response.status_code}（检查 --username/--password 与 --base）"
            )
        return cls(base_url, timeout, session, log)

    def fetch_job_stats(self, workspace_id: str) -> dict[str, int]:
        url = f"{self.base}/api/workspaces/{workspace_id}/stats"
        response = self.session.get(url, timeout=self.timeout)
        if response.status_code != 200:
            _raise_http_error(response, url)
        stats = response.json().get("job_stats") or {}
        return {str(status): int(count) for status, count in stats.items()}

    def fetch_max_items_per_run(self) -> int:
        url = f"{self.base}/api/admin/instance-settings"
        response = self.session.get(url, timeout=self.timeout)
        if response.status_code != 200:
            _raise_http_error(response, url)
        document = response.json()
        try:
            # InstanceSettingsResponse 的 workflows 块在顶层（无
            # executor_runtime 包装键——那是 settings 模块的内部形状，
            # 不是 HTTP 契约；见 instance_settings_contracts.py）。
            return int(document["workflows"]["max_items_per_run"])
        except (KeyError, TypeError, ValueError):
            # 契约漂移时不拦投放：回落默认 2×10^4（#358 的出厂默认），
            # 422 会在首个超量批上立即暴露，错误信息里带正确指引。
            self.log("instance-settings 缺 max_items_per_run，回落默认 20000")
            return 20_000

    def submit_batch(self, workspace_id: str, items: Sequence[dict[str, Any]]) -> dict[str, Any]:
        url = f"{self.base}/api/workspaces/{workspace_id}/runs"
        response = self.session.post(url, json={"items": list(items)}, timeout=None)
        if response.status_code != 200:
            # 全重复吸收（#505 审核 P1-1）：重跑已成功批时服务端对该批
            # 全量 items 已有 job（且非 failed-run 治愈现场）的提交回
            # 400 "No tasks were resolved"——语义是「本批已被完全吸
            # 收」，等价于 created_count=0 的成功，重试永远撞同一堵墙。
            # 这里转成正常应答让游标推进；消息串匹配不优雅，但该串有
            # 服务端测试钉住（见 ABSORBED_BY_DEDUP_MARKER 注释）。
            if response.status_code == 400 and ABSORBED_BY_DEDUP_MARKER in _response_detail(
                response
            ):
                self.log(
                    f"批次提交返回全重复 400（{ABSORBED_BY_DEDUP_MARKER}）："
                    "本批 items 均已有 job（重跑已成功批），视作 created_count=0 跳过"
                )
                return {"run": None, "created_count": 0, "job_ids": []}
            _raise_http_error(response, url)
        result: dict[str, Any] = response.json()
        return result


# ---------------------------------------------------------------------------
# 投放循环
# ---------------------------------------------------------------------------


def run_campaign(
    client: CampaignClient,
    workspace_id: str,
    items: list[dict[str, Any]],
    *,
    watermark: int = DEFAULT_WATERMARK,
    batch_size: int = DEFAULT_BATCH_SIZE,
    poll_interval: float = 10.0,
    retry_wait: float = 5.0,
    retry_max: int = 0,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, int]:
    """执行水位补货循环，返回 {submitted_runs, submitted_items, created_jobs}。

    created_jobs 只累计服务端 created_count；重发批的 dedup 跳过不重复
    计数——这个数字是「本进程净新建 job」的口径，与幂等验收对齐。
    """
    max_items_per_run = client.fetch_max_items_per_run()
    check_batch_size(batch_size, max_items_per_run)
    check_watermark(watermark)

    stats = {"submitted_runs": 0, "submitted_items": 0, "created_jobs": 0}
    cursor = 0
    started = clock()
    client.log(
        f"清单 {len(items)} 个 item，批大小 {batch_size}，水位线 {watermark}，"
        f"轮询间隔 {poll_interval:g}s"
    )

    while cursor < len(items):
        level = non_terminal_count(client.fetch_job_stats(workspace_id))
        remaining = len(items) - cursor
        if level >= watermark:
            client.log(
                f"水位 {level} >= {watermark}，等 {poll_interval:g}s 再查（剩 {remaining} 个 item）"
            )
            sleep(poll_interval)
            continue

        batch = items[cursor : cursor + batch_size]
        if dry_run:
            client.log(
                f"[dry-run] 将提交第 {stats['submitted_runs'] + 1} 批: "
                f"{len(batch)} 个 item（items[{cursor}:{cursor + len(batch)}]），"
                f"当前水位 {level}"
            )
            stats["submitted_runs"] += 1
            stats["submitted_items"] += len(batch)
            cursor += len(batch)
            continue

        attempt = 1
        while True:
            try:
                result = client.submit_batch(workspace_id, batch)
                break
            except SubmitError as exc:
                # 确定性 4xx（401/403/422、非吸收语义的 400 等）重试不会
                # 改变结果——直接失败，错误信息已带修正指引（#505 审核
                # P2-1）。
                if not exc.transient:
                    raise SubmitError(
                        f"批次提交不可恢复失败（游标 items[{cursor}:{cursor + len(batch)}]，"
                        f"修正后重跑同一命令幂等续投）: {exc}"
                    ) from exc
                if retry_max and attempt > retry_max:
                    raise SubmitError(
                        f"批次重试 {retry_max} 次后仍失败（游标 items[{cursor}:"
                        f"{cursor + len(batch)}]，重跑同一命令幂等续投）: {exc}"
                    ) from exc
                wait = min(retry_wait * attempt, MAX_RETRY_WAIT)
                client.log(
                    f"批次提交失败（第 {attempt} 次），{wait:g}s 后重试（dedup 保证不重复）: {exc}"
                )
                sleep(wait)
                attempt += 1
            except Exception as exc:
                # 网络层异常（requests.ConnectionError / Timeout 等，未到
                # HTTP 层）按瞬态重试：水位补货循环本就要求挺过后端重
                # 启，会话级连接抖动是常态；重试同样吃服务端 dedup 幂等。
                # #204 broad-except audit: 这里只捕提交路径上的网络栈错
                # 误，水位查询（fetch_job_stats）不在本循环体内，无吞错
                # 面；KeyboardInterrupt/SystemExit 是 BaseException，不经
                # 此分支。
                if retry_max and attempt > retry_max:
                    raise SubmitError(
                        f"批次重试 {retry_max} 次后仍失败（游标 items[{cursor}:"
                        f"{cursor + len(batch)}]，重跑同一命令幂等续投）: {exc}"
                    ) from exc
                wait = min(retry_wait * attempt, MAX_RETRY_WAIT)
                client.log(
                    f"批次提交网络错误（第 {attempt} 次），{wait:g}s 后重试"
                    f"（dedup 保证不重复）: {exc}"
                )
                sleep(wait)
                attempt += 1

        created = int(result.get("created_count") or 0)
        stats["submitted_runs"] += 1
        stats["submitted_items"] += len(batch)
        stats["created_jobs"] += created
        run_id = str((result.get("run") or {}).get("id") or "已吸收（全重复 400）")
        client.log(
            f"批次 {stats['submitted_runs']} 提交成功: run {run_id}，"
            f"{len(batch)} 个 item，新建 job {created}"
            f"（累计 {stats['created_jobs']}，水位 {level} -> 待重估）"
        )
        # 游标只在批次被服务端接受（2xx，或全重复 400 吸收）后前进——
        # 崩溃重跑时本批整体重发，dedup 过滤（run_service.create_run）
        # 保证已建 job 零重复。
        cursor += len(batch)

    if dry_run:
        client.log(
            f"[dry-run] 完成: 将提交 {stats['submitted_runs']} 批 / "
            f"{stats['submitted_items']} 个 item（未实际投放）"
        )
    else:
        client.log(
            f"完成: {stats['submitted_runs']} 批 / {stats['submitted_items']} 个 item，"
            f"新建 job {stats['created_jobs']}，耗时 {clock() - started:.1f}s"
        )
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="submit-campaign",
        description="水位补货式批量投放（#505 drip-feed submitter）",
    )
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="后端地址")
    parser.add_argument("--username", required=True, help="登录用户名")
    parser.add_argument(
        "--password",
        default=None,
        help=(
            f"登录密码（缺省时读环境变量 {PASSWORD_ENV_VAR}——密码不落 shell history / ps 的方式）"
        ),
    )
    parser.add_argument("--workspace-id", required=True, help="目标 workspace id")
    parser.add_argument(
        "--items",
        required=True,
        help="items 清单文件（.jsonl 每行一个 item；.csv 逐行转 item），契约同 POST /runs",
    )
    parser.add_argument(
        "--watermark",
        type=int,
        default=DEFAULT_WATERMARK,
        help=(
            f"非终态 job 水位线——补货触发阈值，水位低于它就投下一批（默认 {DEFAULT_WATERMARK}，"
            "issue #505 实测标定；#349 红线 5×10^4 之内留 provider 降速缓冲）；"
            "不是容量承诺，低于批大小的低水位线 + 大批次同样是合法的突发配置"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            f"每批 item 数（默认 {DEFAULT_BATCH_SIZE}；须 ≤ workflows.max_items_per_run，"
            "issue 建议 5k–2×10^4）"
        ),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=10.0,
        help="水位满时的轮询间隔秒数（默认 10）",
    )
    parser.add_argument(
        "--retry-wait",
        type=float,
        default=5.0,
        help=f"批提交失败的退避基数秒（默认 5，线性退避，单次等待上限 {MAX_RETRY_WAIT:g}s）",
    )
    parser.add_argument(
        "--retry-max",
        type=int,
        default=0,
        help="批提交失败的最大重试次数（默认 0 = 无限重试；dedup 保证重发幂等）",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="普通请求超时秒（默认 30）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要提交的批次（不登录、不投批）",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    def log(message: str) -> None:
        print(f"[campaign] {message}", flush=True)

    items = load_items(Path(args.items))
    log(f"已加载 {len(items)} 个 item（{args.items}）")

    if args.dry_run:
        log("dry-run: 不登录、不投放，只打印批次计划")

        class _DryRunClient(CampaignClient):
            """dry-run 下的本地桩：不发任何请求，水位恒 0。"""

            def __init__(self) -> None:
                super().__init__(args.base, args.timeout, session=None, log=log)  # type: ignore[arg-type]

            def fetch_max_items_per_run(self) -> int:
                log("dry-run: 跳过 instance-settings 读取，用默认 max_items_per_run=20000")
                return 20_000

            def fetch_job_stats(self, workspace_id: str) -> dict[str, int]:
                return {}

        stats = run_campaign(
            _DryRunClient(),
            args.workspace_id,
            items,
            watermark=args.watermark,
            batch_size=args.batch_size,
            poll_interval=args.poll_interval,
            dry_run=True,
        )
        log(f"dry-run 汇总: {stats['submitted_runs']} 批 / {stats['submitted_items']} 个 item")
        return 0

    password = args.password or os.environ.get(PASSWORD_ENV_VAR) or ""
    if not password:
        raise UsageError(f"缺少密码：传 --password 或设环境变量 {PASSWORD_ENV_VAR}")
    try:
        import requests as requests_module
    except ImportError as exc:  # pragma: no cover - 环境缺依赖的兜底提示
        raise UsageError("缺少 requests 依赖（uv run 环境应已就绪）") from exc
    client = CampaignClient.login(
        args.base,
        args.username,
        password,
        args.timeout,
        requests_module,
        log,
    )
    stats = run_campaign(
        client,
        args.workspace_id,
        items,
        watermark=args.watermark,
        batch_size=args.batch_size,
        poll_interval=args.poll_interval,
        retry_wait=args.retry_wait,
        retry_max=args.retry_max,
    )
    log(f"汇总: {stats}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UsageError as exc:
        print(f"[campaign] 使用错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except SubmitError as exc:
        print(f"[campaign] 提交失败: {exc}", file=sys.stderr)
        raise SystemExit(3) from None
