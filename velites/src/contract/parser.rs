//! Contract source resolution and strict YAML/schema parsing.

use std::path::{Component, Path};

use serde::Deserialize;

use super::{
    Contract, ContractError, ContractSource, FileContract, FileFormat, CONTRACT_DOC, CONTRACT_FILE,
};

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
