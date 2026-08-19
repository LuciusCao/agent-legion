use std::process::Command;

#[test]
fn models_list_json_resolves_registry_and_credentials() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("models.json");
    std::fs::write(
        &path,
        r#"{
          "providers": {
            "sqai": {
              "api": "openai-completions",
              "baseUrl": "https://example.test/v1",
              "apiKey": "$VELITES_TEST_SQAI_KEY",
              "models": ["kimi", {"id":"deepseek","maxOutputTokens":4096}]
            }
          }
        }"#,
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(["models", "list", "--json"])
        .env("VELITES_MODELS_PATH", &path)
        .env("VELITES_TEST_SQAI_KEY", "test-only")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        value,
        serde_json::json!([
            {"provider":"sqai","model":"deepseek"},
            {"provider":"sqai","model":"kimi"}
        ])
    );
}

#[test]
fn models_list_fails_closed_when_credential_reference_is_missing() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("models.json");
    std::fs::write(
        &path,
        r#"{"providers":{"p":{"api":"anthropic-messages","baseUrl":"https://example.test","apiKey":"$VELITES_TEST_MISSING_KEY","models":["m"]}}}"#,
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(["models", "list", "--json"])
        .env("VELITES_MODELS_PATH", &path)
        .env_remove("VELITES_TEST_MISSING_KEY")
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("is not set"));
}
