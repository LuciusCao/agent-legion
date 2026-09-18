//! Read-side budgets for parsed JSON trees, shared by the `json` tool and
//! the output-contract engine (#637 read-side hard caps, attack-report
//! follow-up round).
//!
//! [`MAX_CAPTURE_BYTES`](super::truncate::MAX_CAPTURE_BYTES) bounds the raw
//! file BYTES read into memory; it does not bound what happens afterwards.
//! serde_json's `Value` tree boxes every element (measured: 4 MiB of `1,1,1`
//! parses into a 33.6x-its-bytes heap) and pretty re-serialization widens
//! compact input 2-2.5x for wide/flat shapes and ~123x for deep nesting
//! (one nested container per line pair) — one legal under-cap input could
//! transiently allocate far past the cap (measured 192 MB peak on a 3 MiB
//! file). These budgets hold every shape a `Value` can take:
//!
//! - the tree NODE COUNT (parse side — bounds the boxed-heap total,
//!   independent of how wide or deep the source bytes pack);
//! - the pretty-serialized BYTES (write side — bounds width
//!   amplification, enforced DURING serialization so the transient string
//!   never exceeds the budget either);
//! - the `set` VALUE size (before a model-argument value is spliced into a
//!   tree — upstream only bounds the whole arguments blob at 32 MiB).
//!
//! With all three enforced, peak working memory for one `json` tool call
//! stays within roughly 10x the 4 MiB cap: the tree at ≤ 300k nodes costs
//! ≤ ~32 MiB (measured 56-107 B per node on arm64 depending on shape —
//! wide/flat number arrays at the low end, unique-key objects at the high
//! end; deep nesting adds depth, not per-node cost), the pretty text ≤ 8 MiB,
//! the raw input ≤ 4 MiB. Serialization itself is serde_json driven (a
//! counting/wrapping `io::Write`), so output bytes stay identical to
//! `to_string`/`to_string_pretty`.

use std::io::Write;

use serde_json::Value;

use super::truncate;

/// Budget for one parsed tree: the maximum number of nodes (objects,
/// arrays, strings, numbers, bools, nulls) a document may contain. The tree
/// IS the unit of heap consumption on the parse side: per-node cost is
/// bounded, so a node ceiling bounds the total no matter how compactly or
/// widely the source packs the same node count. 300k nodes ≈ 32 MiB
/// worst-case tree (measured ~107 B per node for unique-key objects, the
/// worst shape; ~8x the byte cap and ~8.2x measured end-to-end) — chosen to
/// stay within the ~10x working-memory goal while refusing the 33.6x
/// all-numbers blowup.
pub const MAX_JSON_NODES: usize = 300_000;

/// Budget for one pretty-serialized JSON document. Pretty printing widens
/// compact input 2-2.5x for wide/flat shapes (each element gains its own
/// line) but ~123x for deep nesting, so the read-side byte cap alone does
/// not bound the output — and no fixed ratio does. The budget is safe
/// because it is enforced DURING serialization (the sink rejects the first
/// byte past it), which bounds the transient string regardless of the
/// amplification ratio; 8 MiB leaves ample headroom over the widest
/// legitimate 300k-node shape (~1.4 MiB pretty).
pub const MAX_PRETTY_BYTES: usize = (truncate::MAX_CAPTURE_BYTES as usize) * 2;

/// Why a tree or serialization was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JsonBudget {
    /// The tree has more than [`MAX_JSON_NODES`] nodes.
    Nodes,
    /// The pretty serialization would exceed [`MAX_PRETTY_BYTES`] bytes.
    PrettyBytes,
    /// The `set` value's compact size exceeds the whole-file read cap.
    ValueBytes,
}

impl std::fmt::Display for JsonBudget {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Nodes => write!(
                f,
                "more than {MAX_JSON_NODES} nodes — the json in-memory tree budget"
            ),
            Self::PrettyBytes => write!(
                f,
                "a serialized size over {} (the json output budget)",
                truncate::format_size(MAX_PRETTY_BYTES)
            ),
            Self::ValueBytes => write!(
                f,
                "a compact size over {} (the json whole-file limit)",
                truncate::MAX_CAPTURE_BYTES_DISPLAY
            ),
        }
    }
}

/// [`parse_bounded`] failure: a syntax problem (reported verbatim by the
/// caller, preserving the pre-budget error wording) or a budget rejection.
#[derive(Debug)]
pub enum ParseBoundedError {
    Syntax(serde_json::Error),
    Budget(JsonBudget),
}

/// Count a tree's nodes, `None` the moment the count passes `max` (early
/// exit: an over-budget tree needs no full walk before rejection). One
/// wrapper node counts per container, matching the allocation it owns; map
/// entries count as nodes too (key string + boxed value).
pub fn count_nodes(value: &Value, max: usize) -> Option<usize> {
    let mut count = 0usize;
    let mut stack: Vec<&Value> = vec![value];
    while let Some(current) = stack.pop() {
        count += 1;
        if count > max {
            return None;
        }
        match current {
            Value::Array(items) => stack.extend(items.iter()),
            Value::Object(map) => {
                count += map.len();
                if count > max {
                    return None;
                }
                stack.extend(map.values());
            }
            _ => {}
        }
    }
    Some(count)
}

/// Parse JSON under the node budget. The byte side is the caller's cap
/// (file read / provider arguments); this is the tree side. A `>max` tree is
/// still parsed first (serde_json has no streaming node cap), but its size
/// is bounded by that parse's own bounded input, so the transient tree
/// cannot exceed what the raw bytes could build. Returns the parsed value
/// together with its node count (the walk already happened — callers that
/// gate on instance size, like the contract engine's noisy-schema path,
/// should not pay for a second one).
pub fn parse_bounded(raw: &str) -> Result<(Value, usize), ParseBoundedError> {
    let value: Value = serde_json::from_str(raw).map_err(ParseBoundedError::Syntax)?;
    match count_nodes(&value, MAX_JSON_NODES) {
        Some(nodes) => Ok((value, nodes)),
        None => Err(ParseBoundedError::Budget(JsonBudget::Nodes)),
    }
}

/// Check one `set` value against the budgets before it is spliced into a
/// tree: the value is model arguments (bounded upstream only by the
/// provider's 32 MiB arguments blob cap), so without its own budget a
/// 32 MiB string could be written into a 4 MiB file (measured: 226 MB
/// peak, 46 MiB on disk). Both checks run BEFORE the tree is mutated, so
/// an oversized value never enters the tree or reaches the disk.
/// The same budgets gate the MERGED root after the splice (codex round-2
/// P2): the file and the value each pass alone while their sum can exceed
/// both (3 MiB object + 2 MiB field, or two <300k-node trees), which
/// would write a file every later json operation rejects.
pub fn check_value_size(value: &Value) -> Result<(), JsonBudget> {
    if count_nodes(value, MAX_JSON_NODES).is_none() {
        return Err(JsonBudget::Nodes);
    }
    let len = compact_len(value);
    if len > truncate::MAX_CAPTURE_BYTES as usize {
        return Err(JsonBudget::ValueBytes);
    }
    Ok(())
}

/// Serialize `value` pretty-printed under [`MAX_PRETTY_BYTES`]. The budget
/// is enforced DURING serialization (see [`serialize`]) — checking the
/// length of a completed string would leave the transient build itself
/// unprotected, which is the exact amplification being closed here.
pub fn serialize_pretty(value: &Value) -> Result<String, JsonBudget> {
    serialize(value, true)
}

/// Serialize `value` (compact or pretty) with the pretty byte budget
/// enforced at the WRITE side: serde_json writes incrementally through the
/// [`io::Write`] sink, so a wrapping sink can reject at the first byte past
/// the budget — the transient string never grows beyond the budget, only
/// the final result is kept. Compact output is not byte-budgeted: it is
/// bounded by the same budget's tree (a compact form of a budgeted tree
/// never exceeds the pretty form of the same tree).
pub fn serialize(value: &Value, pretty: bool) -> Result<String, JsonBudget> {
    let mut sink = BudgetSink::new(pretty);
    let result = if pretty {
        serde_json::to_writer_pretty(&mut sink, value)
    } else {
        serde_json::to_writer(&mut sink, value)
    };
    // Value serialization has no failure mode of its own (all keys are
    // strings, no IO besides the sink); the only reachable error is the
    // sink's budget trip. Anything else still fails closed as a budget
    // rejection rather than silently returning a truncated buffer.
    if result.is_err() || sink.exceeded {
        return Err(JsonBudget::PrettyBytes);
    }
    String::from_utf8(sink.out).map_err(|_| JsonBudget::PrettyBytes)
}

/// Exact compact serialization length of `value`, computed by a counting
/// sink (no materialization — a rejected 32 MiB value must not cost a
/// 32 MiB string just to measure it).
fn compact_len(value: &Value) -> usize {
    struct Counter(usize);
    impl Write for Counter {
        fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
            self.0 += buf.len();
            Ok(buf.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut counter = Counter(0);
    // Infallible: the Counter sink never errors and Value serialization
    // has no failure mode of its own (see `serialize`).
    let _ = serde_json::to_writer(&mut counter, value);
    counter.0
}

/// The wrapping sink: keeps every byte written (the serialization result)
/// while refusing to accept the first byte past the pretty budget.
struct BudgetSink {
    out: Vec<u8>,
    pretty: bool,
    exceeded: bool,
}

impl BudgetSink {
    fn new(pretty: bool) -> Self {
        Self {
            out: Vec::new(),
            pretty,
            exceeded: false,
        }
    }
}

impl Write for BudgetSink {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        if self.pretty && self.out.len() + buf.len() > MAX_PRETTY_BYTES {
            self.exceeded = true;
            return Err(std::io::Error::new(
                std::io::ErrorKind::FileTooLarge,
                "json pretty-output budget exceeded",
            ));
        }
        self.out.extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn count_nodes_counts_every_element_once() {
        assert_eq!(count_nodes(&serde_json::json!(1), 10), Some(1));
        assert_eq!(count_nodes(&serde_json::json!([1, 2]), 10), Some(3));
        // object: container + 2 entries (key strings) + 2 value nodes,
        // the inner array counting its own container node.
        assert_eq!(
            count_nodes(&serde_json::json!({"a": 1, "b": [2]}), 10),
            Some(6)
        );
        assert_eq!(count_nodes(&serde_json::json!("x"), 10), Some(1));
        assert_eq!(count_nodes(&serde_json::json!(null), 10), Some(1));
        // Early exit: budget 1 rejects a 2-node tree without a full walk.
        assert_eq!(count_nodes(&serde_json::json!([1, 2]), 1), None);
    }

    #[test]
    fn parse_bounded_keeps_syntax_errors_distinct_from_budget() {
        // A syntax error must surface as Syntax, not as a budget rejection
        // (the json tool's "not valid JSON" wording depends on it).
        assert!(matches!(
            parse_bounded("{not json"),
            Err(ParseBoundedError::Syntax(_))
        ));
        // The densest valid shape: one node per byte. Over budget → Nodes.
        let dense = format!("[{}]", vec!["1"; MAX_JSON_NODES + 1].join(","));
        assert!(matches!(
            parse_bounded(&dense),
            Err(ParseBoundedError::Budget(JsonBudget::Nodes))
        ));
        // Just under the budget parses fine and reports its node count
        // (one array container + the elements).
        let ok = format!("[{}]", vec!["1"; MAX_JSON_NODES - 1].join(","));
        let (value, nodes) = parse_bounded(&ok).unwrap();
        assert_eq!(nodes, MAX_JSON_NODES);
        assert_eq!(count_nodes(&value, usize::MAX), Some(MAX_JSON_NODES));
    }

    #[test]
    fn serialize_matches_serde_json_for_all_shapes() {
        let cases = vec![
            serde_json::json!({}),
            serde_json::json!([]),
            serde_json::json!(null),
            serde_json::json!(true),
            serde_json::json!(12),
            serde_json::json!(-1.5),
            serde_json::json!("plain"),
            serde_json::json!("\"\\\"\n\t\r\u{08}\u{0c}\u{1f}"),
            serde_json::json!("中文 emoji 😀 key"),
            serde_json::json!({"b": 1, "a": [1, "two", null, {"c": false}], "": 0}),
            serde_json::json!([[[["deep"]]], {"k": [{}]}]),
        ];
        for value in cases {
            assert_eq!(
                serialize(&value, false).unwrap(),
                serde_json::to_string(&value).unwrap(),
                "compact mismatch: {value}"
            );
            assert_eq!(
                serialize(&value, true).unwrap(),
                serde_json::to_string_pretty(&value).unwrap(),
                "pretty mismatch: {value}"
            );
        }
    }

    #[test]
    fn serialize_rejects_pretty_output_past_the_budget() {
        // Width amplification: compact `[1,1,...]` gains 4 bytes per element
        // pretty-printed, so an input around 3.6 MiB stays well under the
        // read cap compact but crosses the 8 MiB output budget pretty.
        let array = format!("[{}]", vec!["1"; 1_800_000].join(","));
        let value: Value = serde_json::from_str(&array).unwrap();
        assert!(serialize(&value, false).is_ok());
        assert_eq!(serialize(&value, true).err(), Some(JsonBudget::PrettyBytes));
    }

    #[test]
    fn check_value_size_accepts_small_and_rejects_large() {
        assert!(check_value_size(&serde_json::json!("x")).is_ok());
        assert!(check_value_size(&serde_json::json!({"a": [1, 2]})).is_ok());
        // Over the node budget.
        let many = serde_json::json!(vec![1; MAX_JSON_NODES + 1]);
        assert_eq!(
            check_value_size(&many).err(),
            Some(JsonBudget::Nodes),
            "node-budget values must be rejected by check_value_size"
        );
        // Over the byte budget: one string of read-cap + 1 bytes (its
        // compact form is larger than the string itself).
        let big = serde_json::json!(
            "x".repeat(usize::try_from(truncate::MAX_CAPTURE_BYTES).unwrap() + 1,)
        );
        assert_eq!(check_value_size(&big).err(), Some(JsonBudget::ValueBytes));
    }

    #[test]
    fn compact_len_matches_the_real_serialization() {
        for value in [
            serde_json::json!("x"),
            serde_json::json!({"a": [1, "two"], "b": null}),
        ] {
            assert_eq!(
                compact_len(&value),
                serde_json::to_string(&value).unwrap().len()
            );
        }
    }
}
