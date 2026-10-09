"""结果元数据 v2 归档成员读回面（#843 PR-1，Host 读侧）。

v2 形态（请求头 ``X-Agent-Result-Format: 2``）：结果元数据 JSON 整体落
结果归档的保留成员 ``result.json``（UTF-8 JSON 文本，shared/
code_contract.RESULT_METADATA_MEMBER），头里只带固定 ASCII 引导值——
``X-Agent-Result`` 头预算（h11 16KiB）与 #748/#755 的降级链随之退役。
本模块只做「从提交的归档（spool/staging 文件）读回该成员」这一件事：

- 流式 ``r|gz`` 扫到成员即停；找到后同样模式的防御性上限
  （超限 = 畸形归档 = 拒收 400，半截 JSON 无法解析、截断无意义，口径
  与 result_output_manifest._MAX_MANIFEST_BYTES 的既有纪律一致）。
- 任何契约违反（缺成员 / 非法 JSON / 非 dict / 超限）都 ValueError
  （带可定位原因）——与 v1 头路径「非法 JSON 即 ValueError → 路由
  400」的语义对齐；归档解包本身的既有错误路径（AgentBundleError 等）
  不在本模块职责内，completion 侧解包不变。
- v1 头里的 ``X-Agent-Result``（若在 v2 请求中仍出现）由路由层忽略：
  v2 下权威在归档成员，本读回面即权威来源。

与 ``result-output-artifacts.json`` 清单通道（#755）互相独立：那是 v1
头溢出的换轨标记语义（头 ``output_artifacts_in_archive`` 布尔 + commit
层读回 enrich）；v2 的 ``result.json`` 里 ``output_artifacts`` 就是完整
字段（无换轨标记语义），同一 ``parse_result_metadata`` 校验链覆盖。
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from shared.code_contract import RESULT_METADATA_MEMBER

# result.json 成员的读取上限：元数据的理论构成是 command 段数、stderr
# tail（4000 字符）与 ≤128 条产物引用——大头撑死 ~50KB；1 MiB 与
# result_output_manifest._MAX_MANIFEST_BYTES 同口径（40 倍余量），超限即
# 畸形归档，拒绝而非截断（半截 JSON 无法解析）。
_MAX_RESULT_METADATA_BYTES = 1024 * 1024


def read_archived_result_metadata(archive: Path) -> str:
    """从结果归档读回 v2 元数据成员的 JSON 文本；违契约即 ValueError。

    返回原始文本（UTF-8 解码后）——校验链（parse_result_metadata）由调用
    方执行，本函数只负责成员定位、大小上限与 JSON 文本形态（先在这里
    拒掉非 JSON 文本，让「非 JSON」与「非法元数据」两类 400 可区分定位）。
    坏归档（非 gzip/tar——tarfile.TarError）同样转 ValueError：v2 的元数据
    权威在归档，归档解不开 = 元数据不可投递，语义对齐 v1 头非法 JSON 的
    ValueError→400，也让 #959 的 4xx 单次降级接管（防 5xx 重试把毒归档
    变成租约过期重排队死循环）。OSError（Host 磁盘面）不在此转换——那是
    500/重试语义，不是 Worker 的错。
    """
    try:
        with tarfile.open(archive, "r|gz") as tar:
            for member in tar:
                if member.name != RESULT_METADATA_MEMBER:
                    continue
                if member.size > _MAX_RESULT_METADATA_BYTES:
                    raise ValueError("archived result metadata member is too large")
                contents = tar.extractfile(member)
                if contents is None:
                    raise ValueError("archived result metadata member is not a file")
                with contents:
                    payload = contents.read(_MAX_RESULT_METADATA_BYTES + 1)
                if len(payload) > _MAX_RESULT_METADATA_BYTES:
                    raise ValueError("archived result metadata member is too large")
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError("archived result metadata member is not valid UTF-8") from exc
                # 文本先过一遍 JSON 合法性（parse 链会再走完整校验）：这里把
                # 「成员非 JSON」与后续「元数据字段非法」分开报错，400 详情可定位。
                try:
                    json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError("archived result metadata member is not valid JSON") from exc
                return text
    except tarfile.TarError as exc:
        raise ValueError(f"result archive is unreadable: {exc}") from exc
    raise ValueError("archived result metadata member is missing")
