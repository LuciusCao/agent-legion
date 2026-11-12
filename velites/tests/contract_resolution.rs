//! Public-API regression tests for source precedence after the module split.

use velites::contract::{Contract, ContractError, ContractSource, CONTRACT_DOC, CONTRACT_FILE};

fn skill_with_both(root: &str, embedded: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join(CONTRACT_FILE), root).unwrap();
    std::fs::create_dir_all(dir.path().join("references")).unwrap();
    std::fs::write(dir.path().join(CONTRACT_DOC), embedded).unwrap();
    dir
}

#[test]
fn valid_root_never_consults_a_malformed_embedded_block() {
    let dir = skill_with_both(
        "files:\n  - path: root.md\n    format: text\n",
        "```yaml contract\nfiles: [\n",
    );

    let contract = Contract::parse(dir.path()).unwrap().unwrap();

    assert_eq!(contract.source(), ContractSource::RootYaml);
    assert_eq!(contract.file_count(), 1);
}

#[test]
fn malformed_root_fails_closed_despite_a_valid_embedded_block() {
    let dir = skill_with_both(
        "files: [",
        "```yaml contract\nfiles:\n  - path: legacy.md\n    format: text\n```\n",
    );

    let error = Contract::parse(dir.path()).unwrap_err();

    assert!(matches!(error, ContractError::Yaml(_)), "{error}");
}

#[test]
fn malformed_legacy_inputs_stay_fail_closed_without_a_root() {
    for document in [
        "```yaml contract\nfiles: [\n```\n",
        "```yaml contract\nfiles:\n  - path: legacy.md\n    format: text\n",
    ] {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("references")).unwrap();
        std::fs::write(dir.path().join(CONTRACT_DOC), document).unwrap();

        assert!(Contract::parse(dir.path()).is_err(), "{document}");
    }
}
