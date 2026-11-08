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
    (
        "files:\n  - path: ''\n    format: text",
        "`path` must be non-empty",
    ),
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
    (
        "files:\n  - path: ../escape.md\n    format: text\n",
        "must not contain `..`",
    ),
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
