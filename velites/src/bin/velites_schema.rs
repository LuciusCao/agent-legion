//! Export the JSON Schema of the event stream (`schemars` derive on
//! `velites::events::Event`). With a path argument the schema is written
//! there; without one it goes to stdout.

fn main() -> anyhow::Result<()> {
    let schema = velites::events::schema_json();
    match std::env::args().nth(1) {
        Some(path) => {
            std::fs::write(&path, format!("{schema}\n"))?;
            eprintln!("wrote {path}");
        }
        None => println!("{schema}"),
    }
    Ok(())
}
