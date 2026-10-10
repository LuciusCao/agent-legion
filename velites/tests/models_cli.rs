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

#[test]
fn gateway_without_registry_ignores_legacy_env_bridge() {
    // #1102: the config.json / VELITES_BASE_URL bridge is removed. Even with
    // both legacy variables set, a direct gateway/openai_compat run without a
    // registry is a harness error (exit 2) pointing at models.json — never a
    // request against the env-supplied endpoint.
    let dir = tempfile::tempdir().unwrap();
    let missing = dir.path().join("missing-models.json");
    for provider in ["gateway", "openai_compat"] {
        let output = Command::new(env!("CARGO_BIN_EXE_velites"))
            .args([
                "--provider",
                provider,
                "--model",
                "m",
                "--no-sandbox",
                "say done",
            ])
            .current_dir(dir.path())
            .env("VELITES_MODELS_PATH", &missing)
            .env("VELITES_BASE_URL", "http://127.0.0.1:9/v1")
            .env("VELITES_API_KEY", "test-only")
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(2), "provider {provider}");
        assert!(output.stdout.is_empty(), "provider {provider}");
        // The error names the registry path, the provider, the migration
        // doc, and the removed bridge so operators know where to move.
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains("missing-models.json"), "{stderr}");
        assert!(stderr.contains(&format!("{provider:?}")), "{stderr}");
        assert!(stderr.contains("velites-model-registry.md"), "{stderr}");
        assert!(stderr.contains("config.json"), "{stderr}");
        assert!(stderr.contains("VELITES_BASE_URL"), "{stderr}");
    }
}
