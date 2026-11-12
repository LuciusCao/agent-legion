//! Parsed-contract execution against a job directory.

use std::path::Path;

use crate::tools::json_limits::{self, ParseBoundedError};
use crate::tools::{resolve_in_cwd, truncate};

use super::{Contract, FileContract, FileFormat, Violation};

impl Contract {
    /// Check every declared file against `job_dir` (canonicalized); an
    /// empty vector means the contract holds.
    pub fn check(&self, job_dir: &Path) -> Vec<Violation> {
        let mut violations = Vec::new();
        for file in &self.files {
            file.check(job_dir, &mut violations);
        }
        violations
    }
}

impl FileContract {
    fn check(&self, job_dir: &Path, violations: &mut Vec<Violation>) {
        let mut push = |message: String| {
            violations.push(Violation {
                path: self.path.clone(),
                message: clip_violation(message),
            })
        };
        // Symlink escapes past the parse-time lexical check are caught here
        // by the same canonicalizing resolver the tools use.
        let resolved = match resolve_in_cwd(job_dir, &self.path) {
            Ok(resolved) => resolved,
            Err(err) => return push(format!("path rejected by the sandbox: {err}")),
        };
        if !resolved.exists() {
            return push("missing required file".into());
        }
        // 攻击报告 HIGH-1（#637 第四读取面）：声明文件的 CONTENT 由模型
        // 产出，end-of-run gate / validate 工具都会执行这里——曾经是无界
        // fs::read（实测 1 GiB JSON → RSS 1.4 GiB）。
        let content = match truncate::read_to_string_bounded(&resolved) {
            Ok(truncate::BoundedRead::Content(content)) => content,
            Ok(truncate::BoundedRead::Oversized) => {
                let limit = truncate::MAX_CAPTURE_BYTES_DISPLAY;
                return push(format!(
                    "file is too large to validate (over the {limit} whole-file limit)"
                ));
            }
            Err(err) => return push(format!("failed to read file: {err}")),
        };
        if content.is_empty() {
            return push("file is empty".into());
        }
        match self.format {
            FileFormat::Text => self.check_text(&content, &mut push),
            FileFormat::Json => self.check_json(&content, &mut push),
        }
    }

    fn check_text(&self, content: &str, push: &mut impl FnMut(String)) {
        if let Some(min) = self.min_chars {
            let chars = content.trim().chars().count();
            if chars < min {
                push(format!(
                    "too short: {chars} characters after trimming, contract requires at least {min}"
                ));
            }
        }
        for heading in &self.required_headings {
            if !content.contains(heading.as_str()) {
                push(format!("missing required heading `{heading}`"));
            }
        }
    }

    fn check_json(&self, content: &str, push: &mut impl FnMut(String)) {
        // 树侧节点预算（与 json 工具同一预算，防 30x+ 高节点数放大）。
        let (instance, nodes) = match json_limits::parse_bounded(content) {
            Ok(parsed) => parsed,
            Err(ParseBoundedError::Syntax(err)) => return push(format!("invalid JSON: {err}")),
            Err(ParseBoundedError::Budget(budget)) => {
                return push(format!("file is too large to validate: {budget}"))
            }
        };
        let schema = self.schema.as_ref().expect("json files carry a schema");
        push_schema_violations(schema, &instance, nodes, push);
    }
}

/// 噪音与内存双上限（#689 review MEDIUM-1）：jsonschema 的 `iter_errors`
/// 在返回迭代器前就把全部错误急切收集进 Vec（实测 ~344 B/条，300k 节点
/// 全违反 124-261 MiB 瞬态），消费侧 100 条封顶封不住生产侧。先走
/// `is_valid` 布尔短路；无效时大实例走 `validate()` 首错短路，小实例保留
/// 逐条明细。
fn push_schema_violations(
    schema: &jsonschema::Validator,
    instance: &serde_json::Value,
    nodes: usize,
    push: &mut impl FnMut(String),
) {
    if schema.is_valid(instance) {
        return;
    }
    if nodes > DETAILED_VIOLATION_NODES {
        // `is_valid` 与 `validate` 恒一致；万一上游不一致，宁可诚实报
        // 失败也不静默放行（空 `first` 分支）。
        let first = schema
            .validate(instance)
            .err()
            .map(|error| format!(" (first violation at `{}`: {error})", instance_at(&error)))
            .unwrap_or_default();
        return push(format!(
            "schema validation failed{first}; the file is too large for \
             a per-item error listing — fix the schema violations and revalidate"
        ));
    }
    // 小实例：逐元素报错的 schema 组合子能产生几十万条 violation，
    // 灌满 remediation 与 Host 记录；头 MAX_VIOLATIONS 条足以驱动修复。
    for (emitted, error) in schema.iter_errors(instance).enumerate() {
        if emitted >= MAX_VIOLATIONS {
            push(format!(
                "schema validation stopped after {MAX_VIOLATIONS} violations \
                 (the file produces more; fix these first and revalidate)"
            ));
            return;
        }
        push(format!(
            "schema violation at `{}`: {error}",
            instance_at(&error)
        ));
    }
}

/// One error's instance path, `/` for the document root (shared by both
/// the per-item listing and the short-circuited first error).
fn instance_at(error: &jsonschema::ValidationError) -> String {
    let path = error.instance_path().to_string();
    if path.is_empty() {
        "/".into()
    } else {
        path
    }
}

/// Cap one violation message: combinators (`allOf`/`contains`) embed the
/// whole offending instance in the error text; untruncated, that dump would
/// balloon the remediation notice and the Host's failure record.
const MAX_VIOLATION_CHARS: usize = 500;

/// Cap how many violations one JSON file's schema walk may emit before
/// the listing stops with an honest truncation note.
const MAX_VIOLATIONS: usize = 100;

/// Node-count threshold over which a FAILING check skips the per-item
/// listing (see `push_schema_violations`): beyond it, jsonschema's eager
/// error collection (~344 B/error, 124-261 MiB transient on a 300k-node
/// all-violating file) would cost more memory than the tree itself; below
/// it the worst eager vector is ~3 MiB — noise, not a memory face — and
/// the actionable per-item listing is kept.
const DETAILED_VIOLATION_NODES: usize = 10_000;

fn clip_violation(message: String) -> String {
    if message.chars().count() <= MAX_VIOLATION_CHARS {
        return message;
    }
    let clipped: String = message.chars().take(MAX_VIOLATION_CHARS).collect();
    let total = message.chars().count();
    format!("{clipped}… [truncated, {total} chars total]")
}
