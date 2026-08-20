//! Runtime-owned LLM provider/model registry.
//!
//! `~/.velites/models.json` is the single source of truth for models that this
//! harness can execute.  The Host only selects `(provider, model)`; endpoint,
//! protocol dialect and credentials stay local to the Worker machine.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context};
use serde::{Deserialize, Serialize};

pub const ENV_MODELS_PATH: &str = "VELITES_MODELS_PATH";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
pub enum ApiKind {
    #[serde(rename = "openai-completions")]
    OpenAiCompletions,
    #[serde(rename = "anthropic-messages")]
    AnthropicMessages,
}

#[derive(Debug, Deserialize)]
pub struct ModelsFile {
    pub providers: BTreeMap<String, ProviderConfig>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProviderConfig {
    pub api: ApiKind,
    pub base_url: String,
    pub api_key: String,
    #[serde(default = "default_anthropic_version")]
    pub anthropic_version: String,
    pub models: Vec<ModelEntry>,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum ModelEntry {
    Id(String),
    Config(ModelConfig),
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ModelConfig {
    pub id: String,
    pub max_output_tokens: Option<u64>,
    #[serde(default)]
    pub thinking_budgets: BTreeMap<String, u64>,
}

impl ModelEntry {
    fn config(&self) -> ModelConfig {
        match self {
            Self::Id(id) => ModelConfig {
                id: id.clone(),
                ..ModelConfig::default()
            },
            Self::Config(config) => config.clone(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ResolvedProvider {
    pub name: String,
    pub api: ApiKind,
    pub base_url: String,
    pub api_key: String,
    pub anthropic_version: String,
    pub model: ModelConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ListedModel {
    pub provider: String,
    pub model: String,
}

pub fn default_path() -> anyhow::Result<PathBuf> {
    if let Some(path) = std::env::var_os(ENV_MODELS_PATH) {
        return Ok(PathBuf::from(path));
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or_else(|| anyhow!("HOME is not set; cannot locate ~/.velites/models.json"))?;
    Ok(home.join(".velites").join("models.json"))
}

pub fn load_default() -> anyhow::Result<ModelsFile> {
    load(&default_path()?)
}

pub fn load(path: &Path) -> anyhow::Result<ModelsFile> {
    warn_if_world_readable(path);
    let raw = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read models config {}", path.display()))?;
    let file: ModelsFile = serde_json::from_str(&raw)
        .with_context(|| format!("invalid JSON in models config {}", path.display()))?;
    validate(&file)?;
    Ok(file)
}

fn warn_if_world_readable(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(metadata) = std::fs::metadata(path) {
            let mode = metadata.permissions().mode();
            if mode & 0o077 != 0 {
                eprintln!(
                    "velites: warning: {} has mode {:o}, expected 0600 (may contain apiKey)",
                    path.display(),
                    mode & 0o777,
                );
            }
        }
    }
}

pub fn list_available(file: &ModelsFile) -> anyhow::Result<Vec<ListedModel>> {
    let mut listed = Vec::new();
    for (provider, config) in &file.providers {
        // Discovery is an executability promise, so unresolved credentials
        // make the runtime probe fail instead of advertising unusable models.
        resolve_api_key(&config.api_key).with_context(|| format!("provider {provider:?}"))?;
        for entry in &config.models {
            listed.push(ListedModel {
                provider: provider.clone(),
                model: entry.config().id,
            });
        }
    }
    listed.sort_by(|a, b| (&a.provider, &a.model).cmp(&(&b.provider, &b.model)));
    Ok(listed)
}

pub fn resolve(file: &ModelsFile, provider: &str, model: &str) -> anyhow::Result<ResolvedProvider> {
    let config = file
        .providers
        .get(provider)
        .ok_or_else(|| anyhow!("provider {provider:?} is not configured in models.json"))?;
    let model_config = config
        .models
        .iter()
        .map(ModelEntry::config)
        .find(|candidate| candidate.id == model)
        .ok_or_else(|| anyhow!("model {provider}/{model} is not configured in models.json"))?;
    Ok(ResolvedProvider {
        name: provider.to_string(),
        api: config.api,
        base_url: config.base_url.clone(),
        api_key: resolve_api_key(&config.api_key)
            .with_context(|| format!("provider {provider:?}"))?,
        anthropic_version: config.anthropic_version.clone(),
        model: model_config,
    })
}

fn validate(file: &ModelsFile) -> anyhow::Result<()> {
    if file.providers.is_empty() {
        return Err(anyhow!("models config must contain at least one provider"));
    }
    for (name, provider) in &file.providers {
        if name.trim().is_empty() || provider.base_url.trim().is_empty() {
            return Err(anyhow!("provider name and baseUrl must not be empty"));
        }
        if provider.api_key.trim().is_empty() {
            return Err(anyhow!("provider {name:?} apiKey must not be empty"));
        }
        if provider.models.is_empty() {
            return Err(anyhow!("provider {name:?} must declare at least one model"));
        }
        let mut ids = std::collections::BTreeSet::new();
        for entry in &provider.models {
            let model = entry.config();
            if model.id.trim().is_empty() || !ids.insert(model.id.clone()) {
                return Err(anyhow!(
                    "provider {name:?} contains an empty or duplicate model id {:?}",
                    model.id
                ));
            }
            if model.max_output_tokens == Some(0)
                || model.thinking_budgets.values().any(|budget| *budget == 0)
            {
                return Err(anyhow!(
                    "provider {name:?} model {:?} token limits must be positive",
                    model.id
                ));
            }
        }
    }
    Ok(())
}

fn resolve_api_key(value: &str) -> anyhow::Result<String> {
    let variable =
        if let Some(variable) = value.strip_prefix("${").and_then(|v| v.strip_suffix('}')) {
            Some(variable)
        } else {
            value.strip_prefix('$')
        };
    match variable {
        Some(name) if !name.is_empty() => std::env::var(name).with_context(|| {
            format!("environment variable {name} referenced by apiKey is not set")
        }),
        Some(_) => Err(anyhow!("apiKey environment reference must name a variable")),
        None => Ok(value.to_string()),
    }
}

fn default_anthropic_version() -> String {
    "2023-06-01".to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> ModelsFile {
        serde_json::from_str(
            r#"{
              "providers": {
                "anthropic": {
                  "api": "anthropic-messages",
                  "baseUrl": "https://api.anthropic.com",
                  "apiKey": "literal-test-key",
                  "models": [
                    "claude-haiku",
                    {"id":"claude-sonnet","maxOutputTokens":8192,"thinkingBudgets":{"high":4096}}
                  ]
                }
              }
            }"#,
        )
        .unwrap()
    }

    #[test]
    fn lists_and_resolves_models() {
        let file = sample();
        assert_eq!(
            list_available(&file).unwrap(),
            vec![
                ListedModel {
                    provider: "anthropic".into(),
                    model: "claude-haiku".into(),
                },
                ListedModel {
                    provider: "anthropic".into(),
                    model: "claude-sonnet".into(),
                },
            ]
        );
        let resolved = resolve(&file, "anthropic", "claude-sonnet").unwrap();
        assert_eq!(resolved.api, ApiKind::AnthropicMessages);
        assert_eq!(resolved.model.max_output_tokens, Some(8192));
    }

    #[test]
    fn rejects_unknown_model_before_network_call() {
        let error = resolve(&sample(), "anthropic", "missing").unwrap_err();
        assert!(error.to_string().contains("is not configured"));
    }
}
