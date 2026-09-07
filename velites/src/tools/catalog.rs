//! Tool catalog self-description (#476): `velites tools list --json`.
//!
//! The catalog is derived from [`ToolKind`] + [`specs::spec`] — zero
//! duplication; the Host-side runtime adapter keeps a static copy of this
//! output and a cross-binary contract test pins the two equal. Consumers:
//! the Host's per-runtime tool directory API and Studio's dynamic tool
//! picker.

use serde::Serialize;

use super::{specs, ToolKind};

/// `velites tools list --json` CLI definition (#476). Lives with the
/// catalog surface (not `cli.rs`) so the subcommand and the data it
/// serves stay one concept — `cli.rs` keeps the agent-run CLI whose
/// file budget is frozen by an earlier exemption (#311).
#[derive(Debug, clap::Parser)]
#[command(name = "velites-tools-list")]
pub struct ToolsListCli {
    /// Emit the tool catalog JSON consumed by the Host / Studio.
    #[arg(long)]
    pub json: bool,
}

/// Selection tier of one tool (#476). `Default` tools form the default
/// `--tools` set (preselected, deselectable); `OptIn` tools are never in
/// the default set but selectable; `Forced` tools are not a user choice at
/// all — the harness advertises them when its activation condition holds
/// (see `lib.rs`: `--require-output` + a parseable contract block),
/// regardless of `--tools`. Lives here, with the catalog surface, so the
/// tier model and its JSON rendering stay one concept (#476).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToolTier {
    Default,
    OptIn,
    Forced,
}

impl ToolTier {
    pub fn wire_name(self) -> &'static str {
        match self {
            Self::Default => "default",
            Self::OptIn => "opt-in",
            Self::Forced => "forced",
        }
    }
}

/// #476 catalog accessors on [`ToolKind`]: a separate impl block here (Rust
/// allows impls in any module of the defining crate) so the tier model and
/// the catalog surface stay one concept without growing `tools/mod.rs`
/// past its file budget.
impl super::ToolKind {
    /// All tool kinds, in catalog order (default, opt-in, forced —
    /// `tools list` emits this order).
    pub fn all() -> [Self; 6] {
        use super::ToolKind::*;
        [Read, Write, Bash, Uuid, Json, Validate]
    }

    /// Selection tier (#476): how the tool enters the advertised set.
    pub fn tier(self) -> ToolTier {
        use super::ToolKind::*;
        match self {
            Read | Write | Bash => ToolTier::Default,
            Uuid | Json => ToolTier::OptIn,
            Validate => ToolTier::Forced,
        }
    }
}

/// One catalog entry. `activation` is present only on the `forced` tier —
/// the CLI flag that activates the tool (the same name the Host dispatch
/// path passes), so the UI can render a locked row with an explanation
/// instead of a checkbox that looks like it could turn validation off.
#[derive(Debug, Serialize)]
pub struct CatalogTool {
    pub name: &'static str,
    pub tier: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub activation: Option<&'static str>,
    pub description: String,
    pub parameters: serde_json::Value,
}

#[derive(Debug, Serialize)]
pub struct ToolCatalog {
    pub version: &'static str,
    pub tools: Vec<CatalogTool>,
}

impl ToolCatalog {
    /// The full catalog, one entry per [`ToolKind`] in declaration order.
    pub fn build() -> Self {
        Self {
            version: env!("CARGO_PKG_VERSION"),
            tools: ToolKind::all()
                .iter()
                .map(|kind| catalog_entry(*kind))
                .collect(),
        }
    }
}

fn catalog_entry(kind: ToolKind) -> CatalogTool {
    let spec = specs::spec(kind);
    CatalogTool {
        name: kind.name(),
        tier: kind.tier().wire_name(),
        activation: match kind.tier() {
            ToolTier::Forced => Some("--require-output"),
            _ => None,
        },
        description: spec.description,
        parameters: spec.parameters,
    }
}

/// Serialize the catalog as the `velites tools list --json` payload.
pub fn to_json() -> anyhow::Result<String> {
    serde_json::to_string(&ToolCatalog::build()).map_err(Into::into)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_covers_every_tool_kind_with_a_tier() {
        let catalog = ToolCatalog::build();
        assert_eq!(catalog.tools.len(), ToolKind::all().len());
        for entry in &catalog.tools {
            assert!(!entry.name.is_empty());
            assert!(matches!(entry.tier, "default" | "opt-in" | "forced"));
        }
    }

    #[test]
    fn default_tier_matches_the_cli_default_tools() {
        // The `--tools` default (read,write,bash) must be exactly the
        // default-tier set — the Studio preselection derives from it.
        let defaults: Vec<&str> = ToolCatalog::build()
            .tools
            .iter()
            .filter(|entry| entry.tier == "default")
            .map(|entry| entry.name)
            .collect();
        assert_eq!(defaults, ["read", "write", "bash"]);
    }

    #[test]
    fn validate_is_forced_with_require_output_activation() {
        let catalog = ToolCatalog::build();
        let entry = catalog
            .tools
            .iter()
            .find(|entry| entry.name == "validate")
            .expect("validate in catalog");
        assert_eq!(entry.tier, "forced");
        assert_eq!(entry.activation, Some("--require-output"));
    }

    #[test]
    fn catalog_serializes_with_version_and_empty_parameters_for_validate() {
        let raw = to_json().unwrap();
        let value: serde_json::Value = serde_json::from_str(&raw).unwrap();
        assert!(value["version"].is_string());
        let tools = value["tools"].as_array().unwrap();
        let validate = tools
            .iter()
            .find(|tool| tool["name"] == "validate")
            .unwrap();
        assert_eq!(validate["parameters"]["properties"], serde_json::json!({}));
        // activation only appears on the forced tier.
        for tool in tools {
            if tool["tier"] != "forced" {
                assert!(tool.get("activation").is_none());
            }
        }
    }
}
