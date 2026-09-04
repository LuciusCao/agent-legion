//! The generic output-contract engine (issue #443, design §8 契约段).
//!
//! Business validation rules stay declarative in the skill repository: a
//! skill's `references/output-contract.md` embeds ONE machine-readable
//! contract block — the first fenced block whose info string is exactly
//! `yaml contract` (the prose around it stays for humans and the model).
//! The harness ships only this engine; the skill owns the rules.
//!
//! Degradation contract: no `output-contract.md` or no contract block in it
//! is `Ok(None)` (the caller falls back to existence checks); a block that
//! exists but is malformed (bad YAML, illegal structure, uncompilable
//! schema) is an explicit `Err` — never a silent downgrade.
//!
//! Three consumers share this one implementation: the `validate` tool
//! (agent self-check), the `--require-output` end-of-run gate, and the
//! `validate` subcommand both binaries expose for the Host-side recheck.

use std::path::{Component, Path, PathBuf};

use serde::Deserialize;

use crate::tools::resolve_in_cwd;

/// Location of the contract document inside a skill directory.
pub const CONTRACT_FILE: &str = "references/output-contract.md";

/// A parsed contract: the file rules declared by the skill.
#[derive(Debug)]
pub struct Contract {
    files: Vec<FileContract>,
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
    /// Parse the contract block of one skill directory. `Ok(None)` signals
    /// "nothing declared here" (missing document or no block); `Err` means a
    /// block exists but is malformed.
    pub fn parse(skill_dir: &Path) -> Result<Option<Contract>, ContractError> {
        let doc_path = skill_dir.join(CONTRACT_FILE);
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
        let mut files = Vec::with_capacity(raw.files.len());
        for file in raw.files {
            files.push(FileContract::parse(file)?);
        }
        if files.is_empty() {
            return Err(ContractError::Structure(
                "`files` must be a non-empty list".into(),
            ));
        }
        Ok(Some(Contract { files }))
    }

    /// Number of declared files (for the "N files checked" success line).
    pub fn file_count(&self) -> usize {
        self.files.len()
    }

    /// Check every declared file against `job_dir` (canonicalized). One
    /// violation per failed rule; an empty vector means the contract holds.
    pub fn check(&self, job_dir: &Path) -> Vec<Violation> {
        let mut violations = Vec::new();
        for file in &self.files {
            file.check(job_dir, &mut violations);
        }
        violations
    }
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
        let bytes = match std::fs::read(&resolved) {
            Ok(bytes) => bytes,
            Err(err) => return push(format!("failed to read file: {err}")),
        };
        if bytes.is_empty() {
            return push("file is empty".into());
        }
        let content = match String::from_utf8(bytes) {
            Ok(content) => content,
            Err(_) => return push("file is not valid UTF-8".into()),
        };
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
        let instance: serde_json::Value = match serde_json::from_str(content) {
            Ok(instance) => instance,
            Err(err) => return push(format!("invalid JSON: {err}")),
        };
        let schema = self.schema.as_ref().expect("json files carry a schema");
        for error in schema.iter_errors(&instance) {
            let path = error.instance_path().to_string();
            push(format!(
                "schema violation at `{}`: {error}",
                if path.is_empty() { "/" } else { &path }
            ));
        }
    }
}

/// Cap one violation message: JSON Schema combinators (`allOf`/`contains`)
/// embed the whole offending instance in the error text — untruncated, that
/// dump would balloon both the remediation notice fed back to the model and
/// the Host's failure record. Char-boundary safe.
const MAX_VIOLATION_CHARS: usize = 500;

fn clip_violation(message: String) -> String {
    if message.chars().count() <= MAX_VIOLATION_CHARS {
        return message;
    }
    let clipped: String = message.chars().take(MAX_VIOLATION_CHARS).collect();
    format!(
        "{clipped}… [truncated, {} chars total]",
        message.chars().count()
    )
}

/// Parse-time lexical escape rejection: contract paths must be relative and
/// contain no `..` (the job dir is only known at check time; symlink escapes
/// are caught there by `resolve_in_cwd`).
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
/// `yaml contract`. Any surrounding prose is ignored; later contract blocks
/// never win over the first one. An opening fence that is never closed is
/// "present but malformed", not "absent" — it fails closed (the module-level
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
/// block short-circuits as `Some(Err(..))` (fail-closed). `None` means no
/// skill directory declared one at all.
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
        std::fs::write(dir.path().join(CONTRACT_FILE), doc).unwrap();
        dir
    }

    fn contract_doc(body: &str) -> String {
        format!(
            "# Output contract\n\nSome prose.\n\n```yaml contract\n{body}\n```\n\nMore prose.\n"
        )
    }

    #[test]
    fn parse_returns_none_without_document_or_block() {
        let dir = tempfile::tempdir().unwrap();
        assert!(Contract::parse(dir.path()).unwrap().is_none());
        let dir = skill_with_doc("# Just prose\n\n```yaml\nfiles: []\n```\n");
        assert!(Contract::parse(dir.path()).unwrap().is_none());
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
    fn parse_surfaces_malformed_blocks_as_errors() {
        let cases = [
            ("not yaml: [", "invalid contract YAML"),
            ("files: []", "non-empty list"),
            ("files:\n  - path: ''\n    format: text", "`path` must be non-empty"),
            (
                "files:\n  - path: a.md\n    format: yaml",
                "`format` must be `text` or `json`",
            ),
            (
                "files:\n  - path: a.json\n    format: json",
                "requires a `schema`",
            ),
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
            (
                "files:\n  - path: a.md\n    format: text\n    bogus: 1",
                "unknown field",
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
        // An opening ```yaml contract fence that never closes is "present but
        // malformed" — it must NOT silently degrade to existence mode.
        let dir = skill_with_doc("# Doc\n\n```yaml contract\nfiles:\n  - path: a.md\n");
        let err = Contract::parse(dir.path()).unwrap_err();
        assert!(
            matches!(err, ContractError::Structure(_)) && err.to_string().contains("never closed"),
            "unclosed fence must be a structure error, got {err}"
        );
    }

    #[test]
    fn check_rejects_symlink_escape_at_check_time() {
        // The parse-time lexical check passes `leak.md`; the check-time
        // canonicalizing resolver must catch the symlink pointing outside.
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
        assert_eq!(violations.len(), 1);
        assert!(
            violations[0]
                .message
                .contains("path rejected by the sandbox"),
            "symlink escape must be rejected, got {:?}",
            violations[0]
        );
    }

    #[test]
    fn check_clips_noisy_schema_error_messages() {
        // allOf/contains violations embed the whole offending instance;
        // the message must be clipped before it reaches the model or the
        // Host failure record.
        let dir = skill_with_doc(&contract_doc(
            "files:\n  - path: big.json\n    format: json\n    schema:\n      allOf:\n        - contains: {const: 1}\n        - contains: {const: 2}\n",
        ));
        let contract = Contract::parse(dir.path()).unwrap().unwrap();
        let job = tempfile::tempdir().unwrap();
        let big: Vec<usize> = vec![0; 2000];
        std::fs::write(
            job.path().join("big.json"),
            serde_json::to_string(&big).unwrap(),
        )
        .unwrap();
        let violations = contract.check(&job.path().canonicalize().unwrap());
        assert!(!violations.is_empty());
        for violation in &violations {
            assert!(
                violation.message.chars().count() <= MAX_VIOLATION_CHARS + 40,
                "violation message must be clipped: {} chars",
                violation.message.chars().count()
            );
            assert!(violation.message.contains("[truncated"));
        }
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

        std::fs::write(job.join("script.md"), "").unwrap();
        let violations = contract.check(&job);
        assert_eq!(violations[0].message, "file is empty");

        std::fs::write(job.join("script.md"), "## 目标\nxy").unwrap();
        let violations = contract.check(&job);
        assert_eq!(violations.len(), 2);
        assert!(violations[0].message.contains("too short: 8 characters"));
        assert!(violations[1]
            .message
            .contains("missing required heading `## 步骤`"));

        std::fs::write(
            job.join("script.md"),
            "## 目标\n## 步骤\nlong enough content",
        )
        .unwrap();
        assert!(contract.check(&job).is_empty());
    }

    #[test]
    fn check_reports_json_errors_with_instance_paths() {
        let dir = skill_with_doc(&contract_doc(
            "files:\n  - path: questions.json\n    format: json\n    schema:\n      type: object\n      required: [exercises]\n      properties:\n        exercises:\n          type: array\n          items: {type: string}\n",
        ));
        let contract = Contract::parse(dir.path()).unwrap().unwrap();
        let job = tempfile::tempdir().unwrap();
        let job = job.path().canonicalize().unwrap();

        std::fs::write(job.join("questions.json"), "{not json").unwrap();
        let violations = contract.check(&job);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.starts_with("invalid JSON:"));

        std::fs::write(job.join("questions.json"), "{\"exercises\": [\"a\", 2]}").unwrap();
        let violations = contract.check(&job);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("/exercises/1"));

        std::fs::write(job.join("questions.json"), "{}").unwrap();
        let violations = contract.check(&job);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("required"));

        std::fs::write(job.join("questions.json"), "{\"exercises\": [\"a\"]}").unwrap();
        assert!(contract.check(&job).is_empty());
    }

    #[test]
    fn first_contract_short_circuits_on_parse_error() {
        let broken = skill_with_doc(&contract_doc("files: ["));
        let fine = skill_with_doc(&contract_doc("files:\n  - path: a.md\n    format: text\n"));
        let none = tempfile::tempdir().unwrap();

        let result = first_contract(&[broken.path().to_path_buf(), fine.path().to_path_buf()]);
        assert!(matches!(result, Some(Err(_))));
        let result = first_contract(&[none.path().to_path_buf(), fine.path().to_path_buf()]);
        assert!(matches!(result, Some(Ok(_))));
        assert!(first_contract(&[none.path().to_path_buf()]).is_none());
    }

    #[test]
    fn gate_outcome_covers_all_three_modes() {
        let job = tempfile::tempdir().unwrap();
        let job = job.path().canonicalize().unwrap();
        assert_eq!(gate_outcome(None, &job), ("existence", Vec::new()));

        let parse_error: Result<Contract, ContractError> =
            Err(ContractError::Structure("x".into()));
        let (mode, violations) = gate_outcome(Some(&parse_error), &job);
        assert_eq!(mode, "contract");
        assert_eq!(
            violations,
            vec!["contract parse error: invalid contract structure: x"]
        );

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
        assert!(message.contains("a.txt"));
        assert!(message.contains("1) b.json: missing required file"));
        assert!(message.ends_with("then stop."));
        // Single-class messages stay well-formed too.
        let only_missing = remediation_message(&["a.txt".to_string()], &[]);
        assert!(only_missing.contains("missing: a.txt"));
        assert!(!only_missing.contains("contract"));
    }
}
