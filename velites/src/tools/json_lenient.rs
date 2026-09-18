//! #747 lenient parsing for the `json` tool's `set` op: salvaging
//! double-encoded container text (a string holding JSON array/object text)
//! into the container it holds — when that parse is lossless.
//!
//! `set`'s `value` accepts any JSON value, but models sometimes double-encode
//! containers — passing `"[\"1.5\", \"2.5\"]"` (a string holding JSON text)
//! instead of `["1.5", "2.5"]`. Writing that verbatim used to succeed
//! silently, leaving the agent unable to tell a tool bug from its own
//! argument mistake and burning turns re-probing (observed in production as
//! multi-turn probe loops). `set` salvages such strings into the container
//! they hold and says so in its output — but only when the parse is lossless
//! (every number must round-trip) and within a size gate; otherwise the
//! string is written literally with a note explaining why. The salvage
//! decision and its three-way outcome live here; the notes' wording and the
//! write path stay in `json.rs`'s `set`.

use serde_json::Value;

/// #747 lenient-parse attempt gate: `set`'s double-encoded-container
/// salvage only tries to parse value strings up to this size (the mistake
/// form it serves — a model re-encoding a small container — is KB-scale);
/// anything larger keeps the literal-write semantics and only skips the
/// parse attempt. Independently named and anchored to #747 on purpose:
/// the unbounded-read hard caps live in json_limits (#637, fail-closed
/// budgets that reject the whole value/tree) — this gate is an earlier,
/// softer decision (drop a best-effort fallback, not the operation), so
/// the two must not share a code path. 256 KiB bounds the transient parse
/// DOM at roughly 4 MiB even at the worst measured amplification (17x on
/// all-number arrays, #637's measurements: a 100 MB value string cost
/// 8.9 s parse + 1.7 GB DOM).
pub(super) const MAX_LENIENT_PARSE_BYTES: usize = 256 * 1024;

/// #747 宽容解析的结果三分支：解析成功（无损、闸内）/ 不构成解析候选 /
/// 构成候选但放弃解析（每分支都有专属注记，见 `json.rs` 的 `set`）。
/// 只认容器——标量形态的字符串（"123"、"true"、"null"）不是候选：字符串
/// 字段装着数字样文本是合法数据，生产观察到的失误形态只有容器二次编码。
/// 设计上不给「字面容器文本」留带内逃生语法：候选且可无损解析的一律解析，
/// 逃生通道是 write 工具，注记与 schema description 都明说，行为可预期。
pub(super) enum LenientParse {
    /// 候选成立且每个数字可无损往返：解析出的容器。
    Container(Value),
    /// 不是候选（不以 `[`/`{` 开头，或不是合法 JSON 容器）：按字面写入，
    /// 无注记。
    NotCandidate,
    /// 候选成立但放弃：内容超闸（`true`）或含不可无损往返的数字（`false`），
    /// 按字面写入并注记原因。
    Declined { oversized: bool },
}

/// #747 宽容解析：`value` 为字符串且 trim（Unicode 空白容忍）后恰为合法
/// JSON 数组/对象文本（模型二次编码容器的常见失误）时，解析为该容器。
/// 两个前置拒绝，都是 #747 对抗 review 的修复：
///
/// - **长度闸**（[`MAX_LENIENT_PARSE_BYTES`]`）：超大字符串不尝试解析——
///   兜底不是主路径，大 payload 的字面写入语义不变，同时封死无界
///   parse + DOM 放大（#637 形态：100 MB 字符串实测 8.9 s + 1.7 GB）。
/// - **数字无损往返**：serde_json（未开 arbitrary_precision）把浮点降级
///   为 f64、超 u64/i64 的整数降级为 f64，重序列化会变形（实测
///   `1.0000000000000001`→`1.0`、`1e2`→`100.0`、30 位大整数→
///   `1.2345678901234568e+29`）。逐个数字 token 校验
///   `to_string(parse(token)) == token`，任一失败即放弃解析——二次编码
///   失误的常规形态（`["1.5", "2.5"]` 这类短数字）不受影响，字面意图的
///   高精度数字零损坏。
pub(super) fn parse_double_encoded_container(value: &Value) -> LenientParse {
    let Some(text) = value.as_str() else {
        return LenientParse::NotCandidate;
    };
    let trimmed = text.trim();
    if !trimmed.starts_with(['[', '{']) {
        return LenientParse::NotCandidate;
    }
    if trimmed.len() > MAX_LENIENT_PARSE_BYTES {
        return LenientParse::Declined { oversized: true };
    }
    let Ok(container @ (Value::Array(_) | Value::Object(_))) =
        serde_json::from_str::<Value>(trimmed)
    else {
        // `[1, 2] extra` 这类开头像容器、整体不是合法 JSON 的文本：不是
        // 二次编码形态，按字面写入。
        return LenientParse::NotCandidate;
    };
    if !numbers_round_trip(trimmed) {
        return LenientParse::Declined { oversized: false };
    }
    LenientParse::Container(container)
}

/// 逐数字 token 的无损往返校验（#747）：扫描 JSON 文本中字符串字面量与
/// 结构以外的数字 token，`Number` 解析→重序列化→与原文比对。等价于对
/// 整个值做「解析→重序列化→与原文比较」但只对数字生效——字符串值经
/// 解析天然无损（`\"` / `\uXXXX` 等转义形式等价），数字才会在 f64 降级
/// 时变形；按 token 比对使带空格与转义的常规二次编码形态（如
/// `["1.5", "2.5"]`、json.dumps 的 ensure_ascii 输出）不被误判。
fn numbers_round_trip(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'"' => {
                // 跳过字符串字面量：其中的数字是字符串内容，不参与校验。
                i += 1;
                while i < bytes.len() && bytes[i] != b'"' {
                    if bytes[i] == b'\\' {
                        i += 1; // 转义字符（含 \"），跳过下一个字节
                    }
                    i += 1;
                }
                i += 1; // 收尾引号
            }
            b'-' | b'0'..=b'9' => {
                let start = i;
                while i < bytes.len()
                    && !matches!(bytes[i], b',' | b']' | b'}' | b' ' | b'\t' | b'\n' | b'\r')
                {
                    i += 1;
                }
                // 已过 serde_json::from_str 的整体校验，token 必然可解析；
                // 比对失败即降级变形（f64 取整、科学计数改写等）。
                let token = &text[start..i];
                let Ok(number) = serde_json::from_str::<serde_json::Number>(token) else {
                    return false;
                };
                if serde_json::to_string(&number).ok().as_deref() != Some(token) {
                    return false;
                }
            }
            _ => i += 1,
        }
    }
    true
}

/// Human-readable form of [`MAX_LENIENT_PARSE_BYTES`] for the oversized
/// note (#747) — keeps the gate constant single-sourced.
pub(super) fn format_gate_size() -> String {
    format!("{}KB", MAX_LENIENT_PARSE_BYTES / 1024)
}
