"""S3 jobs GC 的纯 key 文法与豁免判定。"""

from __future__ import annotations

JOBS_PREFIX = "jobs/"
STAGING_PREFIX = "jobs-staging/"


def _material_generation(segments: list[str]) -> str:
    """材料 key 第 3 段的代次数字：``g{n}``（#1267 旧文法）或 ``v2g{n}``
    （#1279 现文法）；非材料形态（段数不足/段形不符）返回 ''。"""
    seg = segments[2] if len(segments) >= 4 else ""
    return seg[3:] if seg.startswith("v2g") else seg[1:] if seg.startswith("g") else ""


def material_shaped_key(key: str) -> bool:
    """碰撞命名空间里的材料 key 形态（#1282 r4 P1）：第 3 段是 g{代次} 或
    v2g{代次}（#1279）。

    材料 key 为 ``{workspace_id}/{hash|material_id}/(v2)g{代次}/[m_{material_id}/]{filename}``
    （两种文法本判定都匹配——碰撞分流要收全形态，删除门禁在
    ``material_key_single_use``）；workspace id 可字面为 jobs / jobs-staging
    （无保留名约束），那样的材料 key 落在 jobs 前缀扫描内。真 job 产物同
    位置是 job id（``{ws}_{workflow_key}_{source_id}``，恒含下划线），永不
    匹配 (v2)g{纯数字}——分流零误伤。legacy 无代次段的材料 key 不可复用
    （新上传恒带代次段），留在删除批安全。
    """
    return _material_generation(key.split("/")).isdigit()


def material_key_single_use(key: str) -> bool:
    """#1279 新文法的材料 key 跨行不可复用——材料删除批的准入门禁
    （#1314：``s3_material_gc`` 的分流与删除动作处复核；不满足的材料
    孤儿只报告不删除）。

    形态：``{ws}/{hash|id}/v2g{代次}/m_{material_id}/{filename}``。删行后
    同文件同名再上传也必得新 key，revalidate→删 窗口随之构造性关闭。
    旧文法（g 段非 v2g 形态、legacy 无代次段 key、inline-{uuid4} key）
    一律排除。本判定与 materials.py（presign/重试）、material_repair.py、
    material_object_publish.py（inline/demo seed 共用的写入 chokepoint）
    的 key 文法（以及 ``material_shaped_key`` 碰撞分流面）必须同步演进。

    构造性关闭论证（#1279 codex r1/r2 P1 族——「第 4 段标记」连续两轮被
    「旧文法合法文件名巧合」反例击穿后，标记移入写端不可注入的第 3 段；
    #1314 重开删除批时此论证是承重墙）：

    1. 逐条核验在役旧写端产线的第 3 段能力：presign 首发与重试
       （materials.py）第 3 段是服务端派生的字面量 ``'g' || generation``
       ——#1267 起恒为 ``g<int>``；inline（run_text_batch）旧形态第 3 段
       是 ``inline-{uuid4}``；demo seed 旧形态无代次段（第 3 段是文件名
       首段）。没有任何在役写端能在第 3 段写出 ``v2g0``。
    2. 混合版本窗口内的活写端只有紧邻旧版 Host server（材料 key 写端全
       在 Host 单副本后端：presign/inline/demo seed 都在 server 侧，
       worker/ 零写端）——它受 #1267 文法约束，第 3 段发不出 ``v2g``；
       新版 GC CLI 与旧 Host 并行时，旧 Host 产出的全部 key 都被本门禁
       拦在只报告侧。例外是 #1312 期 Host：已发 ``v2g`` key 但 inline/demo
       seed 仍先 PUT 后建行——秒级窗口由材料年龄下限兜住（#1314）。
    3. #1267 之前的 legacy 写端已死（不存在于任何与现行代码的混合部署）；
       其桶内遗留即使有形态巧合（如文件名首段字面 ``v2g0``），有行则非
       孤儿（key_exists/新鲜反查救下），无行则是无人能重建引用的真孤儿
       ——删除安全。
    4. 因此「fresh_revalidate 后旧写端重建同名 key + PUT」对带 v2g 标记
       的 key 构造性不成立：重建者只能是现版写端，而现版写端对新行恒
       生成新 material_id、必得新 key。
    5. 现版写端行先于对象（#1314）：key 先被已提交的行（含 uploading
       intent）引用、后 PUT——反查「无行」即该 key 此后永不再被引用。
    """
    segments = key.split("/")
    mid = segments[3] if len(segments) >= 5 else ""
    new_gen = len(segments) >= 5 and segments[2].startswith("v2g")
    hex_ok = mid[:2] == "m_" and len(mid) == 34 and all(c in "0123456789abcdef" for c in mid[2:])
    return new_gen and _material_generation(segments).isdigit() and hex_ok


def job_authority_key_single_use(key: str) -> bool:
    """#853 起的一次性 authority key：``.v`` 后是 32 位小写 hex attempt。

    只有该文法可进入 authority 删除批。legacy 固定 key 仍出现在孤儿报告中，
    但其同名重建的反查到删除窗口不能靠一次反查关闭，故只报告不删除。版本段
    严格匹配 ``uuid4().hex``，伪造/残缺 ``.v`` 形态不会绕过门禁；名称可含
    多段路径与末尾 ``.gz`` 形式标记。
    """
    segments = key.split("/")
    attempt = segments[4] if len(segments) >= 6 else ""
    return (
        len(segments) >= 6
        and all(segments[:3])
        and segments[0] == JOBS_PREFIX.rstrip("/")
        and segments[3] == ".v"
        and len(attempt) == 32
        and all(c in "0123456789abcdef" for c in attempt)
        and all(segments[5:])
    )


def _rollback_backup_exempt(key: str) -> bool:
    """``/.rollback/`` 回滚备份豁免的判定（codex #774 P1 族，#1285/#1295 收窄）。

    豁免仅对 jobs/ 与 jobs-staging/ 前缀的 key 生效——promote 回滚备份只
    出现在这两个产物前缀下（恢复最终失败时它是幸存清单行所指向旧字节的
    最后恢复源）。两类 key 不适用：

    - 材料根前缀的 key（#1285）：材料文件名允许合法多段相对路径、无
      .rollback 保留段，``{ws}/{hash}/g{代次}/dir/.rollback/f.bin`` 形态
      的旧代次/删行残留若继承本豁免，会在材料孤儿删除批重开（#1314）
      后被永久跳过；
    - 撞名前缀下的材料形态 key（#1295）：workspace id 字面为
      jobs / jobs-staging 时材料 key 落在产物前缀内，同样允许合法
      .rollback 段；若豁免先于材料形态分流命中，该 key 既不删除也不进
      报告——永久不可见。豁免前先识别材料形态（第 3 段 (v2)g{代次}）排除
      之，staging 道扫描期按材料宽限窗分流进材料候选（#1322，收口纪律
      见 #1282）。
    """
    return (
        key.startswith((JOBS_PREFIX, STAGING_PREFIX))
        and "/.rollback/" in key
        and not material_shaped_key(key)
    )
