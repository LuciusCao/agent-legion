use std::process::ExitCode;

use clap::Parser;

#[tokio::main]
async fn main() -> ExitCode {
    // `velites sandbox wrap -- <cmd>` runs one command inside the OS sandbox;
    // it is dispatched before the agent-run CLI parse because that CLI
    // requires a positional instruction.
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("models") {
        if args.get(2).map(String::as_str) != Some("list") {
            eprintln!("error: expected `velites models list --json`");
            return ExitCode::from(2);
        }
        let parse_args = std::iter::once(args[0].clone()).chain(args.into_iter().skip(3));
        let cli = velites::cli::ModelsListCli::parse_from(parse_args);
        if !cli.json {
            eprintln!("error: `velites models list` requires --json");
            return ExitCode::from(2);
        }
        return match velites::models::load_default()
            .and_then(|file| velites::models::list_available(&file))
            .and_then(|models| serde_json::to_string(&models).map_err(Into::into))
        {
            Ok(output) => {
                println!("{output}");
                ExitCode::SUCCESS
            }
            Err(err) => {
                eprintln!("error: {err:#}");
                ExitCode::from(2)
            }
        };
    }
    if args.get(1).map(String::as_str) == Some("sandbox") {
        if args.get(2).map(String::as_str) != Some("wrap") {
            eprintln!("error: expected `velites sandbox wrap --cwd <dir> -- <cmd...>`");
            return ExitCode::from(2);
        }
        let parse_args = std::iter::once(args[0].clone()).chain(args.into_iter().skip(3));
        let cli = velites::cli::SandboxWrapCli::parse_from(parse_args);
        return match velites::run_sandbox_wrap(cli) {
            Ok(code) => ExitCode::from(code),
            Err(err) => {
                eprintln!("error: {err:#}");
                ExitCode::from(2)
            }
        };
    }
    let cli = velites::cli::Cli::parse();
    match velites::run(cli).await {
        Ok(code) => ExitCode::from(code),
        Err(err) => {
            eprintln!("error: {err:#}");
            ExitCode::from(2)
        }
    }
}
