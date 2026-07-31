//! Gateway credentials (design §7 凭据配置).
//!
//! Source of truth: `~/.velites/config.json` (expected mode 0600) holding
//! `base_url` + `api_key`. Environment variables `VELITES_BASE_URL` /
//! `VELITES_API_KEY` override the file values — this is the seam a future
//! vault integration plugs into. Secrets never appear on the command line.
//!
//! The pure functions (`load_file`, `merge`) take explicit inputs so tests
//! never touch the real home directory or process environment.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context};
use serde::Deserialize;

pub const ENV_BASE_URL: &str = "VELITES_BASE_URL";
pub const ENV_API_KEY: &str = "VELITES_API_KEY";

/// Resolved gateway credentials.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GatewayCredentials {
    pub base_url: String,
    pub api_key: String,
}

/// Raw `~/.velites/config.json` contents; either field may be overridden by
/// the environment, so both are optional at this layer.
#[derive(Debug, Default, Deserialize)]
pub struct FileConfig {
    pub base_url: Option<String>,
    pub api_key: Option<String>,
}

/// The default config location for a given home directory.
pub fn config_path(home: &Path) -> PathBuf {
    home.join(".velites").join("config.json")
}

/// Read and parse a config file. A missing file is a clear, actionable
/// error (harness fault → non-zero exit), not a silent default.
pub fn load_file(path: &Path) -> anyhow::Result<FileConfig> {
    if !path.exists() {
        return Err(anyhow!(
            "gateway credentials not found: {} does not exist\n\
             create it with mode 0600, e.g.:\n  \
             install -m 600 /dev/null {0} && \
             printf '{{\"base_url\": \"https://<gateway>\", \"api_key\": \"<key>\"}}' > {0}\n\
             or set {ENV_BASE_URL} and {ENV_API_KEY} in the environment",
            path.display(),
        ));
    }
    warn_if_world_readable(path);
    let raw = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read gateway config {}", path.display()))?;
    let config: FileConfig = serde_json::from_str(&raw)
        .with_context(|| format!("invalid JSON in gateway config {}", path.display()))?;
    Ok(config)
}

/// Env overrides file, field by field. Both fields must resolve to a
/// non-empty value.
pub fn merge(
    file: FileConfig,
    env_base_url: Option<String>,
    env_api_key: Option<String>,
) -> anyhow::Result<GatewayCredentials> {
    let base_url = env_base_url.or(file.base_url).ok_or_else(|| {
        anyhow!(
            "gateway base_url missing: set {ENV_BASE_URL} or `base_url` in ~/.velites/config.json"
        )
    })?;
    let api_key = env_api_key.or(file.api_key).ok_or_else(|| {
        anyhow!("gateway api_key missing: set {ENV_API_KEY} or `api_key` in ~/.velites/config.json")
    })?;
    if base_url.is_empty() || api_key.is_empty() {
        return Err(anyhow!(
            "gateway credentials must not be empty (base_url / api_key)"
        ));
    }
    Ok(GatewayCredentials { base_url, api_key })
}

/// Production entry point: `$HOME/.velites/config.json` + process env.
pub fn resolve() -> anyhow::Result<GatewayCredentials> {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or_else(|| anyhow!("HOME is not set; cannot locate ~/.velites/config.json"))?;
    // The env may fully supply credentials without any file on disk.
    let env_base_url = std::env::var(ENV_BASE_URL).ok();
    let env_api_key = std::env::var(ENV_API_KEY).ok();
    let path = config_path(&home);
    let file = if path.exists() {
        load_file(&path)?
    } else if env_base_url.is_some() && env_api_key.is_some() {
        FileConfig::default()
    } else {
        load_file(&path)? // produces the actionable missing-file error
    };
    merge(file, env_base_url, env_api_key)
}

/// Warn (never fail) when the secret file is readable by group/others.
fn warn_if_world_readable(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(metadata) = std::fs::metadata(path) {
            let mode = metadata.permissions().mode();
            if mode & 0o077 != 0 {
                eprintln!(
                    "velites: warning: {} has mode {:o}, expected 0600 (contains api_key)",
                    path.display(),
                    mode & 0o777,
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_config(dir: &Path, body: &str) -> PathBuf {
        let path = config_path(dir);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, body).unwrap();
        path
    }

    #[test]
    fn loads_base_url_and_key() {
        let dir = tempfile::tempdir().unwrap();
        let path = write_config(
            dir.path(),
            r#"{"base_url": "https://gw.example/v1", "api_key": "sk-test"}"#,
        );
        let file = load_file(&path).unwrap();
        let creds = merge(file, None, None).unwrap();
        assert_eq!(creds.base_url, "https://gw.example/v1");
        assert_eq!(creds.api_key, "sk-test");
    }

    #[test]
    fn missing_file_is_actionable_error() {
        let dir = tempfile::tempdir().unwrap();
        let err = load_file(&config_path(dir.path())).unwrap_err();
        let text = err.to_string();
        assert!(text.contains("does not exist"), "got: {text}");
        assert!(text.contains(ENV_API_KEY), "got: {text}");
    }

    #[test]
    fn invalid_json_is_clear_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = write_config(dir.path(), "not json");
        let err = load_file(&path).unwrap_err();
        assert!(err.to_string().contains("invalid JSON"));
    }

    #[test]
    fn env_overrides_file_per_field() {
        let file = FileConfig {
            base_url: Some("https://file.example".into()),
            api_key: Some("file-key".into()),
        };
        let creds = merge(file, Some("https://env.example".into()), None).unwrap();
        assert_eq!(creds.base_url, "https://env.example");
        assert_eq!(creds.api_key, "file-key");
    }

    #[test]
    fn missing_fields_error_out() {
        let err = merge(FileConfig::default(), None, None).unwrap_err();
        assert!(err.to_string().contains("base_url missing"));
        let err = merge(
            FileConfig {
                base_url: Some("https://x".into()),
                api_key: None,
            },
            None,
            None,
        )
        .unwrap_err();
        assert!(err.to_string().contains("api_key missing"));
        let err = merge(
            FileConfig {
                base_url: Some(String::new()),
                api_key: Some("k".into()),
            },
            None,
            None,
        )
        .unwrap_err();
        assert!(err.to_string().contains("must not be empty"));
    }
}
