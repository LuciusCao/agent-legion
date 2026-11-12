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

use std::path::PathBuf;

mod parser;
#[cfg(test)]
mod tests;
mod validation;

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

impl Contract {
    /// Where this contract was read from (#542 migration signal surface).
    pub fn source(&self) -> ContractSource {
        self.source
    }

    /// Number of declared files (for the "N files checked" success line).
    pub fn file_count(&self) -> usize {
        self.files.len()
    }
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
