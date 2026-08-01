//! The committed JSON schema must stay in sync with the serde definitions in
//! `src/events.rs`. Regenerate with:
//!
//! ```sh
//! cargo run --bin velites-schema -- schema/events.schema.json
//! ```

#[test]
fn committed_schema_is_current() {
    let committed = include_str!("../schema/events.schema.json");
    let generated = format!("{}\n", velites::events::schema_json());
    assert_eq!(
        committed.trim_end(),
        generated.trim_end(),
        "schema/events.schema.json is stale; regenerate with \
         `cargo run --bin velites-schema -- schema/events.schema.json`"
    );
}
