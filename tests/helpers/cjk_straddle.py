"""#755 终审 P2-1 复现形态共享桩：骑跨 8KB 字节切割点的 CJK 单行构造器。"""

from __future__ import annotations


def cjk_line_with_straddling_secret(secret: str) -> str:
    """4100 个字符的 CJK 单行（12300 字节——字符数 ≤ 8192 但字节数 > 8192，
    恰好绕过旧的字符口径闸门），自定义形态密钥（非内建 sk-/ghp_ 形态，
    只能整值字面匹配）的中点骑跨 8KB 字节切割点。"""
    from shared.pi_events import STDERR_TAIL_BYTES

    total_bytes = 4100 * 3
    cut_at = total_bytes - STDERR_TAIL_BYTES  # 4108
    pre_bytes = cut_at - len(secret.encode()) // 2  # 密钥中点对齐切割点
    # CJK 主体 + ASCII 微调，把密钥起点精确放到 pre_bytes。
    line = "噪" * (pre_bytes // 3) + "a" * (pre_bytes % 3) + secret
    remaining = total_bytes - len(line.encode())
    line += "噪" * (remaining // 3) + "a" * (remaining % 3)
    assert len(line.encode()) == total_bytes
    assert len(line) <= STDERR_TAIL_BYTES  # 字符口径在预算内——旧闸门的盲区
    return line
