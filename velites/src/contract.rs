//! The generic output-contract engine (issue #443, design §8 契约段;
//! #542 contract location migration).
//!
//! Business validation rules stay declarative in the skill repository; the
//! harness ships only this engine; the skill owns the rules. Since #542 the
//! machine-readable contract is a standalone root file — `contract.yaml` —
//! and the legacy location (a fenced block with info string exactly
//! `yaml contract` inside `references/output-contract.md`, the first such
//! block winning) is deprecated but still honored.
//!
//! Three-tier resolution (#542), in priority order:
//!
//! 1. `contract.yaml` in the skill root EXISTS → it is the sole authority.
//!    A malformed file (bad YAML, illegal structure, uncompilable schema,
//!    unreadable/non-UTF-8) is an explicit `Err` — fail-closed, never a
//!    silent downgrade, and the embedded block is NOT consulted even when
//!    present (root wins completely: no parsing, no error).
//! 2. No `contract.yaml`, but `references/output-contract.md` embeds a
//!    contract block → the block is parsed as before (deprecated source).
//! 3. Neither → `Ok(None)` (the caller falls back to existence checks).
//!
//! Degradation contract unchanged: "nothing declared here" is `Ok(None)`
//! while "present but malformed" is always `Err` — never a silent downgrade.
//!
//! Three consumers share this one implementation: the `validate` tool
//! (agent self-check), the `--require-output` end-of-run gate, and the
//! `validate` subcommand both binaries expose for the Host-side recheck.
//! Read-side caps (#637 attack follow-up): file CONTENT is model-controlled
//! even though the paths are skill-controlled, so the check uses the same
//! bounded reader + tree budget as the read/json tools and caps the
//! violation count a noisy schema emits — too large is an honest violation,
//! never a truncated pretend-pass. A failing check on a LARGE instance
//! short-circuits on the first error instead (jsonschema collects every
//! error eagerly, #689 review MEDIUM-1) — see `push_schema_violations`.

use std::path::{Component, Path, PathBuf};

use serde::Deserialize;

use crate::tools::json_limits::{self, ParseBoundedError};
use crate::tools::{resolve_in_cwd, truncate};

/// Location of the machine-readable contract (normative since #542): a
/// standalone YAML file in the skill root.
pub const CONTRACT_FILE: &str = "contract.yaml";
/// Location of the deprecated legacy contract document (pre-#542): the
/// embedded ```yaml contract block inside this markdown file still wins
/// when no root `contract.yaml` exists.
pub const CONTRACT_DOC: &str = "references/output-contract.md";

/// Where a parsed contract came from (#542): the validate subcommand prints
/// a deprecation signal for the embedded-block source (stdout only; the
/// Host reads exit code + stderr, so the extra line is inert there).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContractSource {
    /// The skill-root `contract.yaml` (normative location).
    RootYaml,
    /// A ```yaml contract block embedded in `references/output-contract.md`
    /// (deprecated; migration signal is emitted, semantics unchanged).
    EmbeddedBlock,
}

/// A parsed contract: the file rules declared by the skill.
#[derive(Debug)]
pub struct Contract {
    files: Vec<FileContract>,
    source: ContractSource,
}

#[derive(Debug)]
struct FileContract {
    /// Path relative to the job dir (validated at parse: relative, no `..`).
    path: String,
    format: FileFormat,
    /// `text` only: minimum trimmed character count.
    min_chars: Option<usize>,
    /// `text` only: strings that must appear verbatim in the content.
    required_headings: Vec<String>,
    /// `json` only: compiled JSON Schema (draft 2020-12).
    schema: Option<jsonschema::Validator>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FileFormat {
    Text,
    Json,
}

/// One violated rule, phrased so a model can act on it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Violation {
    pub path: String,
    pub message: String,
}

impl std::fmt::Display for Violation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.path, self.message)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ContractError {
    #[error("failed to read {path}: {source}")]
    Io {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error("invalid contract YAML: {0}")]
    Yaml(#[from] serde_yaml::Error),
    #[error("invalid contract structure: {0}")]
    Structure(String),
    #[error("invalid JSON Schema for `{path}`: {message}")]
    Schema { path: String, message: String },
}

/// Raw YAML shape of the contract block (strict: unknown keys rejected).
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ContractYaml {
    files: Vec<FileYaml>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FileYaml {
    path: String,
    format: String,
    min_chars: Option<usize>,
    required_headings: Option<Vec<String>>,
    schema: Option<serde_json::Value>,
}

impl Contract {
    /// Parse the contract of one skill directory, three-tier fallback (#542):
    /// `Ok(None)` = nothing declared; `Err` = present but malformed — a
    /// broken root file fails closed regardless of any embedded block.
    pub fn parse(skill_dir: &Path) -> Result<Option<Contract>, ContractError> {
        // Tier 1: the skill-root contract.yaml. Existence (not mere
        // readability) decides the tier: a present-but-broken root file is
        // an error even when a valid embedded block exists — root wins.
        let root_path = skill_dir.join(CONTRACT_FILE);
        let root_content = match read_root_contract(&root_path) {
            Ok(Some(content)) => content,
            Ok(None) => {
                // Tier 2: fall back to the embedded block.
                return parse_embedded_block(skill_dir);
            }
            Err(source) => {
                return Err(ContractError::Io {
                    path: root_path,
                    source,
                })
            }
        };
        let raw: ContractYaml = serde_yaml::from_str(&root_content)?;
        Ok(Some(Self::from_raw(raw, ContractSource::RootYaml)?))
    }

    /// Where this contract was read from (#542 migration signal surface).
    pub fn source(&self) -> ContractSource {
        self.source
    }

    /// Number of declared files (for the "N files checked" success line).
    pub fn file_count(&self) -> usize {
        self.files.len()
    }

    /// Check every declared file against `job_dir` (canonicalized); an
    /// empty vector means the contract holds.
    pub fn check(&self, job_dir: &Path) -> Vec<Violation> {
        let mut violations = Vec::new();
        for file in &self.files {
            file.check(job_dir, &mut violations);
        }
        violations
    }

    fn from_raw(raw: ContractYaml, source: ContractSource) -> Result<Contract, ContractError> {
        let mut files = Vec::with_capacity(raw.files.len());
        for file in raw.files {
            files.push(FileContract::parse(file)?);
        }
        if files.is_empty() {
            return Err(ContractError::Structure(
                "`files` must be a non-empty list".into(),
            ));
        }
        Ok(Contract { files, source })
    }
}

/// Tier-1 file read: `Ok(None)` = no root contract.yaml (tier 2 applies);
/// any other I/O failure is the caller's `Err` (fail-closed).
fn read_root_contract(path: &Path) -> Result<Option<String>, std::io::Error> {
    match std::fs::read_to_string(path) {
        Ok(content) => Ok(Some(content)),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(err) => Err(err),
    }
}

/// Tier 2 (deprecated since #542): the embedded ```yaml contract block;
/// none → `Ok(None)`, an unclosed fence → fail-closed `Err`.
fn parse_embedded_block(skill_dir: &Path) -> Result<Option<Contract>, ContractError> {
    let doc_path = skill_dir.join(CONTRACT_DOC);
    let content = match std::fs::read_to_string(&doc_path) {
        Ok(content) => content,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(ContractError::Io {
                path: doc_path,
                source,
            })
        }
    };
    let Some(block) = extract_contract_block(&content)? else {
        return Ok(None);
    };
    let raw: ContractYaml = serde_yaml::from_str(&block)?;
    Ok(Some(Contract::from_raw(
        raw,
        ContractSource::EmbeddedBlock,
    )?))
}

impl FileContract {
    fn parse(raw: FileYaml) -> Result<FileContract, ContractError> {
        if raw.path.trim().is_empty() {
            return Err(ContractError::Structure("`path` must be non-empty".into()));
        }
        reject_escape(&raw.path)?;
        let format = match raw.format.as_str() {
            "text" => FileFormat::Text,
            "json" => FileFormat::Json,
            other => {
                return Err(ContractError::Structure(format!(
                    "`format` must be `text` or `json`, got `{other}`"
                )))
            }
        };
        if format != FileFormat::Text
            && (raw.min_chars.is_some() || raw.required_headings.is_some())
        {
            return Err(ContractError::Structure(
                "`min_chars`/`required_headings` only apply to `format: text`".into(),
            ));
        }
        let schema = match (format, raw.schema) {
            (FileFormat::Json, Some(schema)) => Some(compile_schema(&raw.path, &schema)?),
            (FileFormat::Json, None) => {
                return Err(ContractError::Structure(format!(
                    "`format: json` requires a `schema` (missing for `{}`)",
                    raw.path
                )))
            }
            (FileFormat::Text, Some(_)) => {
                return Err(ContractError::Structure(
                    "`schema` only applies to `format: json`".into(),
                ))
            }
            (FileFormat::Text, None) => None,
        };
        Ok(FileContract {
            path: raw.path,
            format,
            min_chars: raw.min_chars,
            required_headings: raw.required_headings.unwrap_or_default(),
            schema,
        })
    }

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
/// Parse-time lexical escape rejection: paths must be relative, no `..`
/// (symlink escapes are caught at check time by `resolve_in_cwd`).
fn reject_escape(path: &str) -> Result<(), ContractError> {
    let raw = Path::new(path);
    if raw.is_absolute() {
        return Err(ContractError::Structure(format!(
            "`path` must be relative to the job dir, got absolute `{path}`"
        )));
    }
    if raw.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(ContractError::Structure(format!(
            "`path` must not contain `..`, got `{path}`"
        )));
    }
    Ok(())
}

fn compile_schema(
    path: &str,
    schema: &serde_json::Value,
) -> Result<jsonschema::Validator, ContractError> {
    jsonschema::options()
        .with_draft(jsonschema::Draft::Draft202012)
        .build(schema)
        .map_err(|err| ContractError::Schema {
            path: path.to_string(),
            message: err.to_string(),
        })
}

/// Extract the FIRST fenced block whose info string is exactly
/// `yaml contract`; later blocks never win. An opening fence that is never
/// closed is "present but malformed" — fail-closed (the module-level
/// degradation promise only covers a genuinely missing block).
fn extract_contract_block(markdown: &str) -> Result<Option<String>, ContractError> {
    let mut lines = markdown.lines();
    while let Some(line) = lines.next() {
        if line.trim() != "```yaml contract" {
            continue;
        }
        let mut body = Vec::new();
        for body_line in lines.by_ref() {
            if body_line.trim_start().starts_with("```") {
                return Ok(Some(body.join("\n")));
            }
            body.push(body_line);
        }
        return Err(ContractError::Structure(
            "contract block opening fence ```yaml contract is never closed".into(),
        ));
    }
    Ok(None)
}

/// The first skill directory that DECLARES a contract wins; a malformed
/// block short-circuits as `Some(Err(..))`; `None` = none declared one.
pub fn first_contract(skill_dirs: &[PathBuf]) -> Option<Result<Contract, ContractError>> {
    for dir in skill_dirs {
        match Contract::parse(dir) {
            Ok(Some(contract)) => return Some(Ok(contract)),
            Ok(None) => {}
            Err(err) => return Some(Err(err)),
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contract_gate::{gate_outcome, remediation_message};

    fn skill_with_doc(doc: &str) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("references")).unwrap();
        std::fs::write(dir.path().join(CONTRACT_DOC), doc).unwrap();
        dir
    }

    fn skill_with_root_contract(body: &str) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join(CONTRACT_FILE), body).unwrap();
        dir
    }

    fn contract_doc(body: &str) -> String {
        let doc = "# Output contract\n\nSome prose.\n\n```yaml contract\n";
        format!("{doc}{body}\n```\n\nMore prose.\n")
    }

    /// Structure rules shared by both contract locations (#542).
    const STRICT_CASES: &[(&str, &str)] = &[
        ("not yaml: [", "invalid contract YAML"),
        ("files: []", "non-empty list"),
        ("files:\n  - path: ''\n    format: text", "`path` must be non-empty"),
        ("files:\n  - path: a.md\n    format: yaml", "`format` must be `text` or `json`"),
        ("files:\n  - path: a.json\n    format: json", "requires a `schema`"),
        ("files:\n  - path: a.md\n    format: text\n    schema: {type: object}", "`schema` only applies to `format: json`"),
        ("files:\n  - path: a.json\n    format: json\n    min_chars: 5\n    schema: {type: object}", "only apply to `format: text`"),
        ("files:\n  - path: a.json\n    format: json\n    schema: {type: nope}", "invalid JSON Schema"),
        ("files:\n  - path: a.md\n    format: text\n    bogus: 1", "unknown field"),
        ("files:\n  - path: ../escape.md\n    format: text\n", "must not contain `..`"),
    ];

    fn check_structure_cases(cases: &[(&str, &str)]) {
        for (body, needle) in cases {
            // Root location, then the embedded-block location.
            for err in [
                Contract::parse(skill_with_root_contract(body).path())
                    .expect_err(&format!("root must fail: {body}")),
                Contract::parse(skill_with_doc(&contract_doc(body)).path())
                    .expect_err(&format!("block must fail: {body}")),
            ] {
                assert!(
                    err.to_string().contains(needle),
                    "error `{err}` must mention `{needle}`"
                );
            }
        }
    }

    #[test]
    fn parse_returns_none_without_document_or_block() {
        let dir = tempfile::tempdir().unwrap();
        assert!(Contract::parse(dir.path()).unwrap().is_none());
        let dir = skill_with_doc("# Just prose\n\n```yaml\nfiles: []\n```\n");
        assert!(Contract::parse(dir.path()).unwrap().is_none());
    }

    /// Skill dir with BOTH a root contract and an embedded block.
    fn root_and_embedded(root_body: &str, embedded_body: &str) -> tempfile::TempDir {
        let dir = skill_with_root_contract(root_body);
        std::fs::create_dir_all(dir.path().join("references")).unwrap();
        std::fs::write(dir.path().join(CONTRACT_DOC), contract_doc(embedded_body)).unwrap();
        dir
    }

    #[test]
    fn parse_prefers_the_root_contract_yaml() {
        let dir = root_and_embedded(
            "files:\n  - path: root.md\n    format: text\n",
            "files:\n  - path: embedded.md\n    format: text\n",
        );
        let contract = Contract::parse(dir.path()).unwrap().unwrap();
        assert_eq!(contract.source(), ContractSource::RootYaml);
        assert_eq!(contract.files.len(), 1);
        assert_eq!(contract.files[0].path, "root.md");

        // And it is actually enforced against a job dir (root.md missing).
        let job = tempfile::tempdir().unwrap();
        let job = job.path().canonicalize().unwrap();
        assert_eq!(contract.check(&job)[0].message, "missing required file");
        std::fs::write(job.join("root.md"), "content").unwrap();
        std::fs::write(job.join("embedded.md"), "content").unwrap();
        assert!(contract.check(&job).is_empty());
    }

    #[test]
    fn parse_root_contract_wins_even_when_malformed() {
        // Root present but broken: fail-closed on the ROOT file alone.
        let dir = root_and_embedded(
            "files: [",
            "files:\n  - path: embedded.md\n    format: text\n",
        );
        let err = Contract::parse(dir.path()).unwrap_err();
        assert!(
            matches!(err, ContractError::Yaml(_)),
            "malformed root must surface its own YAML error, got {err}"
        );
    }

    #[test]
    fn parse_root_contract_runs_the_same_structure_rules() {
        // Every STRICT_CASE asserted at BOTH locations by the helper.
        check_structure_cases(STRICT_CASES);
    }

    #[test]
    fn parse_surfaces_malformed_blocks_as_errors() {
        // Embedded-block failures: bad YAML, schema-on-text, and friends.
        let cases = [
            ("not yaml: [", "invalid contract YAML"),
            (
                "files:\n  - path: a.md\n    format: text\n    schema: {type: object}",
                "`schema` only applies to `format: json`",
            ),
            (
                "files:\n  - path: a.json\n    format: json\n    min_chars: 5\n    schema: {type: object}",
                "only apply to `format: text`",
            ),
            (
                "files:\n  - path: a.json\n    format: json\n    schema: {type: nope}",
                "invalid JSON Schema",
            ),
        ];
        for (body, needle) in cases {
            let dir = skill_with_doc(&contract_doc(body));
            let err = Contract::parse(dir.path()).expect_err(&format!("block must fail: {body}"));
            assert!(
                err.to_string().contains(needle),
                "error `{err}` must mention `{needle}`"
            );
        }
    }

    #[test]
    fn parse_falls_back_to_the_deprecated_embedded_block() {
        let dir = skill_with_doc(&contract_doc("files:\n  - path: a.md\n    format: text\n"));
        let contract = Contract::parse(dir.path()).unwrap().unwrap();
        assert_eq!(contract.source(), ContractSource::EmbeddedBlock);
        assert_eq!(contract.files[0].path, "a.md");
    }

    #[test]
    fn parse_uses_the_first_contract_block() {
        let mut doc = contract_doc("files:\n  - path: a.md\n    format: text\n");
        doc.push_str("\n```yaml contract\nfiles:\n  - path: b.md\n    format: text\n```\n");
        let dir = skill_with_doc(&doc);
        let contract = Contract::parse(dir.path()).unwrap().unwrap();
        assert_eq!(contract.files.len(), 1);
        assert_eq!(contract.files[0].path, "a.md");
    }

    #[test]
    fn parse_rejects_escaping_paths() {
        for path in ["/etc/passwd", "../escape.md", "a/../../b.md"] {
            let body = format!("files:\n  - path: \"{path}\"\n    format: text\n");
            let dir = skill_with_doc(&contract_doc(&body));
            let err = Contract::parse(dir.path()).unwrap_err();
            assert!(
                matches!(err, ContractError::Structure(_)),
                "{path} must be a structure error, got {err}"
            );
        }
    }

    #[test]
    fn parse_fails_closed_on_unclosed_fence() {
        // An unclosed opening fence is "present but malformed".
        let dir = skill_with_doc("# Doc\n\n```yaml contract\nfiles:\n  - path: a.md\n");
        let err = Contract::parse(dir.path()).unwrap_err();
        assert!(
            matches!(err, ContractError::Structure(_)) && err.to_string().contains("never closed"),
            "unclosed fence must be a structure error, got {err}"
        );
    }

    #[test]
    fn check_rejects_symlink_escape_at_check_time() {
        // Parse-time lexical check passes; check-time resolver must not.
        let dir = skill_with_doc(&contract_doc(
            "files:\n  - path: leak.md\n    format: text\n",
        ));
        let contract = Contract::parse(dir.path()).unwrap().unwrap();
        let job = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        std::fs::write(outside.path().join("secret.md"), "x".repeat(100)).unwrap();
        std::os::unix::fs::symlink(outside.path().join("secret.md"), job.path().join("leak.md"))
            .unwrap();
        let violations = contract.check(&job.path().canonicalize().unwrap());
        assert_eq!(
            violations.len(),
            1,
            "symlink escape rejected: {violations:?}"
        );
        assert!(violations[0]
            .message
            .contains("path rejected by the sandbox"));
    }

    #[test]
    fn check_reports_missing_empty_and_text_rules() {
        let dir = skill_with_doc(&contract_doc(
            "files:\n  - path: script.md\n    format: text\n    min_chars: 10\n    required_headings: [\"## 目标\", \"## 步骤\"]\n",
        ));
        let contract = Contract::parse(dir.path()).unwrap().unwrap();
        let job = tempfile::tempdir().unwrap();
        let job = job.path().canonicalize().unwrap();

        let violations = contract.check(&job);
        assert_eq!(violations.len(), 1);
        assert_eq!(violations[0].message, "missing required file");

        // (content, expected count, one needle per violation).
        for (content, count, needles) in [
            ("", 1, &["file is empty"][..]),
            (
                "## 目标\nxy",
                2,
                &[
                    "too short: 8 characters",
                    "missing required heading `## 步骤`",
                ],
            ),
            ("## 目标\n## 步骤\nlong enough content", 0, &[][..]),
        ] {
            std::fs::write(job.join("script.md"), content).unwrap();
            let violations = contract.check(&job);
            assert_eq!(violations.len(), count, "for {content:?}");
            for (violation, needle) in violations.iter().zip(needles) {
                assert!(violation.message.contains(needle), "{violations:?}");
            }
        }
    }

    #[test]
    fn check_reports_json_errors_with_instance_paths() {
        let dir = skill_with_doc(&contract_doc(
            "files:\n  - path: questions.json\n    format: json\n    schema:\n      type: object\n      required: [exercises]\n      properties:\n        exercises:\n          type: array\n          items: {type: string}\n",
        ));
        let contract = Contract::parse(dir.path()).unwrap().unwrap();
        let job = tempfile::tempdir().unwrap();
        let job = job.path().canonicalize().unwrap();

        // (content, the needle its single violation must mention).
        for (content, needle) in [
            ("{not json", "invalid JSON:"),
            ("{\"exercises\": [\"a\", 2]}", "/exercises/1"),
            ("{}", "required"),
        ] {
            std::fs::write(job.join("questions.json"), content).unwrap();
            let violations = contract.check(&job);
            assert_eq!(violations.len(), 1, "for {content}");
            assert!(violations[0].message.contains(needle), "{violations:?}");
        }
        // Valid instance: no violations.
        std::fs::write(job.join("questions.json"), "{\"exercises\": [\"a\"]}").unwrap();
        assert!(contract.check(&job).is_empty());
    }

    #[test]
    fn first_contract_short_circuits_on_parse_error() {
        let broken = skill_with_doc(&contract_doc("files: ["));
        let fine = skill_with_doc(&contract_doc("files:\n  - path: a.md\n    format: text\n"));
        let none = tempfile::tempdir().unwrap();

        let p = |d: &tempfile::TempDir| d.path().to_path_buf();
        assert!(matches!(
            first_contract(&[p(&broken), p(&fine)]),
            Some(Err(_))
        ));
        assert!(matches!(first_contract(&[p(&none), p(&fine)]), Some(Ok(_))));
        assert!(first_contract(&[p(&none)]).is_none());
    }

    #[test]
    fn gate_outcome_covers_all_three_modes() {
        let job = tempfile::tempdir().unwrap().path().canonicalize().unwrap();
        assert_eq!(gate_outcome(None, &job), ("existence", Vec::new()));

        let parse_error: Result<_, _> = Err(ContractError::Structure("x".into()));
        let (mode, violations) = gate_outcome(Some(&parse_error), &job);
        assert_eq!(mode, "contract");
        assert_eq!(violations.len(), 1);
        assert!(violations[0].contains("contract parse error"));

        let dir = skill_with_doc(&contract_doc("files:\n  - path: a.md\n    format: text\n"));
        let contract = Contract::parse(dir.path()).unwrap().unwrap();
        let ok: Result<Contract, ContractError> = Ok(contract);
        let (mode, violations) = gate_outcome(Some(&ok), &job);
        assert_eq!(mode, "contract");
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn remediation_message_lists_missing_and_violations() {
        let message = remediation_message(
            &["a.txt".to_string()],
            &["b.json: missing required file".to_string()],
        );
        assert!(message.starts_with("SYSTEM NOTICE:"));
        assert!(message.contains("1) b.json: missing required file"));
        assert!(message.ends_with("then stop."));
        // Single-class messages stay well-formed too.
        let only_missing = remediation_message(&["a.txt".to_string()], &[]);
        assert!(only_missing.contains("missing: a.txt"));
        assert!(!only_missing.contains("contract"));
    }
}
